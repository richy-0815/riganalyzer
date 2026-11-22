"""Diesel-Landesschnitt fuer Daenemark, Schweden und Norwegen (seit v0.32.0).

Freie Preise je Tankstelle gibt es dort (noch) nicht – also wenigstens der amtliche Landesdurchschnitt statt des
deutschen Profilpreises:
- DK, SE: EU Weekly Oil Bulletin (Europaeische Kommission, „prices with taxes“, woechentlich, CC BY 4.0)
- NO: Statistisk sentralbyraa, Tabelle 09654 (Autodiesel, Monatsschnitt, CC BY 4.0)
- Umrechnung NOK -> EUR: EZB-Referenzkurs (taeglich)
Die Seite rechnet damit wie mit dem Profilpreis (plus Auf-/Abschlag je Tankstellenart), zeigt ihn aber als
Schaetzung („Landesschnitt“), nicht als echten Preis. Ablage: data/cache/fuel-nordic.json, taeglich erneuert.

Daenemark bekommt spaeter echte Preise (Pflicht-APIs der Ketten seit BEK 1351/2025, siehe fuelprice_dk.py).
"""

import io
import json
import logging
import os
import re
import threading
import time
import zipfile
import xml.etree.ElementTree as ET

import httpx

log = logging.getLogger("fuelprice")

TTL = 24 * 3600
STALE = 21 * 86400          # aelter als 3 Wochen: lieber gar nichts als Uraltwerte
UA = {"User-Agent": "RigAnalyzer/1 (riganalyzer.petrick.top; Tankplan)"}
EC_PAGE = "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"
SSB_TABLE = "https://data.ssb.no/api/v0/no/table/09654/"
ECB_XML = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
COUNTRIES = {"DK": "Denmark", "SE": "Sweden"}
# grob (lat min/max, lon min/max) – die genaue Zuordnung macht der Geofabrik-Katalog (regions.country_at)
BOX = {"DK": (54.4, 57.9, 7.8, 15.3), "SE": (55.2, 69.2, 10.8, 24.3), "NO": (57.8, 71.3, 4.4, 31.3)}
GEOFABRIK = {"denmark": "DK", "sweden": "SE", "norway": "NO"}
SOURCE = {"DK": "Landesschnitt (EU Weekly Oil Bulletin)", "SE": "Landesschnitt (EU Weekly Oil Bulletin)",
          "NO": "Landesschnitt (SSB, Monatsschnitt)"}
SHORT = {"DK": "Landesschnitt EU", "SE": "Landesschnitt EU", "NO": "Landesschnitt SSB"}

_lock = threading.Lock()
_state = {"t": 0.0, "avg": {}, "loading": False}


def _file():
    return os.path.join(os.environ.get("CACHE_DIR", os.path.join(os.environ.get("DATA_DIR", "/data"), "cache")),
                        "fuel-nordic.json")


# ---------------------------------------------------------------- XLSX ohne Zusatzpaket (zip + XML)
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _xlsx_rows(data):
    """Erstes Tabellenblatt als Liste von Zeilen (Strings/Zahlen), leere Zellen = None."""
    z = zipfile.ZipFile(io.BytesIO(data))
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):  # noqa: S314 – expat ohne externe Entities, Datei der Behoerde
            strings.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
    sheet = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
    rows = []
    for row in ET.fromstring(z.read(sheet)).iter("{%s}row" % _NS["m"]):  # noqa: S314 – expat ohne externe Entities, Datei der Behoerde
        cells = {}
        for c in row.findall("m:c", _NS):
            ref = re.match(r"([A-Z]+)", c.get("r", "A")).group(1)
            col = 0
            for ch in ref:
                col = col * 26 + ord(ch) - 64
            v = c.find("m:v", _NS)
            if c.get("t") == "s" and v is not None:
                val = strings[int(v.text)]
            elif c.get("t") == "inlineStr":
                val = "".join(t.text or "" for t in c.iter("{%s}t" % _NS["m"]))
            elif v is not None:
                try:
                    val = float(v.text)
                except (TypeError, ValueError):
                    val = v.text
            else:
                val = None
            cells[col - 1] = val
        rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return rows


def parse_bulletin(data):
    """„Weekly prices with taxes“ -> {"DK": €/l, "SE": €/l}. Spalte „Automotive gas oil“ (Diesel) in €/1000 l."""
    rows = _xlsx_rows(data)
    col = None
    for r in rows:
        for i, v in enumerate(r):
            if isinstance(v, str) and re.search(r"automotive\s+gas\s*oil|gas\s*oil\s+automobile|gasoil\s+auto", v, re.I):
                col = i
                break
        if col is not None:
            break
    if col is None:
        raise ValueError("Diesel-Spalte nicht gefunden")
    out = {}
    for r in rows:
        name = next((v for v in r[:3] if isinstance(v, str) and v.strip()), "")
        for cc, en in COUNTRIES.items():
            if name.strip().lower().startswith(en.lower()) and cc not in out and col < len(r):
                v = r[col]
                if isinstance(v, str):
                    try:
                        v = float(v.replace(".", "").replace(",", ".")) if v.count(",") == 1 else float(v.replace(",", ""))
                    except ValueError:
                        v = None
                if isinstance(v, (int, float)):
                    eur = v / 1000 if v > 50 else v      # €/1000 l (Standard) oder schon €/l
                    if 0.8 < eur < 4:
                        out[cc] = round(eur, 3)
    return out


def _bulletin():
    page = httpx.get(EC_PAGE, headers=UA, timeout=30, follow_redirects=True)
    page.raise_for_status()
    m = re.search(r'href="([^"]+?\.xlsx[^"]*)"[^>]*>[^<]*(?:with\s+taxes)', page.text, re.I) or \
        re.search(r'href="([^"]*document/download[^"]*with%20Taxes[^"]*)"', page.text, re.I)
    if not m:
        raise ValueError("Bulletin-Link nicht gefunden")
    url = m.group(1).replace("&amp;", "&")
    if url.startswith("/"):
        url = "https://energy.ec.europa.eu" + url
    r = httpx.get(url, headers=UA, timeout=60, follow_redirects=True)
    r.raise_for_status()
    return parse_bulletin(r.content)


# ---------------------------------------------------------------- Norwegen (SSB) + Kurs (EZB)
def parse_ecb(xml_text):
    """EZB-Tageskurse -> {"NOK": 11.6, …} (Einheiten je 1 EUR)."""
    return {m.group(1): float(m.group(2)) for m in re.finditer(r"currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([\d.]+)['\"]", xml_text)}


def parse_ssb_meta(meta):
    """Tabellen-Metadaten -> (Variablen-Code, Wert-Code) fuer Autodiesel."""
    for var in meta.get("variables", []):
        for code, text in zip(var.get("values", []), var.get("valueTexts", [])):
            if "diesel" in text.lower() and "anlegg" not in text.lower():
                return var["code"], code
    raise ValueError("Diesel in SSB 09654 nicht gefunden")


def parse_ssb_value(js):
    vals = js.get("value") or []
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        raise ValueError("SSB ohne Wert")
    return float(vals[-1])


def _norway(nok_per_eur):
    meta = httpx.get(SSB_TABLE, headers=UA, timeout=30)
    meta.raise_for_status()
    var, code = parse_ssb_meta(meta.json())
    q = {"query": [{"code": var, "selection": {"filter": "item", "values": [code]}},
                   {"code": "Tid", "selection": {"filter": "top", "values": ["1"]}}],
         "response": {"format": "json-stat2"}}
    r = httpx.post(SSB_TABLE, json=q, headers=UA, timeout=30)
    r.raise_for_status()
    nok = parse_ssb_value(r.json())
    return round(nok / nok_per_eur, 3)


_fx = {"t": 0.0, "rates": {}}


def fx():
    """EZB-Tageskurse (Einheiten je 1 EUR), 12 h gemerkt – {} wenn nicht erreichbar."""
    if time.time() - _fx["t"] > 12 * 3600:
        try:
            r = httpx.get(ECB_XML, headers=UA, timeout=20)
            r.raise_for_status()
            _fx.update(t=time.time(), rates=parse_ecb(r.text))
        except (httpx.HTTPError, ValueError) as e:
            log.warning("EZB-Kurse nicht abrufbar: %s", e)
            _fx["t"] = time.time() - 11 * 3600   # in einer Stunde nochmal
    return _fx["rates"]


def _fetch():
    avg = {}
    try:
        avg.update(_bulletin())
    except (httpx.HTTPError, ValueError, KeyError, IndexError, zipfile.BadZipFile, ET.ParseError) as e:
        log.warning("Landesschnitt DK/SE nicht abrufbar: %s", e)
    try:
        avg["NO"] = _norway(fx()["NOK"])
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("Landesschnitt NO nicht abrufbar: %s", e)
    return avg


def _load_file():
    try:
        with open(_file(), encoding="utf-8") as f:
            d = json.load(f)
        _state.update(t=float(d.get("t", 0)), avg=d.get("avg") or {})
    except (OSError, ValueError):
        pass


def _refresh():
    try:
        avg = _fetch()
        if avg:
            _state.update(t=time.time(), avg={**_state["avg"], **avg})
            os.makedirs(os.path.dirname(_file()), exist_ok=True)
            with open(_file() + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"t": _state["t"], "avg": _state["avg"]}, f)
            os.replace(_file() + ".tmp", _file())
            log.info("Diesel-Landesschnitt: %s", _state["avg"])
    finally:
        _state["loading"] = False


BACKGROUND = True


def averages():
    """{"DK": €/l, …} – aus dem Merker; ist er aelter als ein Tag, im Hintergrund erneuern."""
    with _lock:
        if not _state["t"]:
            _load_file()
        due = time.time() - _state["t"] > TTL and not _state["loading"]
        if due:
            _state["loading"] = True
    if due:
        if BACKGROUND:
            threading.Thread(target=_refresh, name="fuel-nordic", daemon=True).start()
        else:
            _refresh()
    if time.time() - _state["t"] > STALE:
        return {}
    return dict(_state["avg"])


def _country(lat, lon):
    hits = [cc for cc, b in BOX.items() if b[0] <= lat <= b[1] and b[2] <= lon <= b[3]]
    if not hits:
        return None
    import regions
    r = regions.country_at(lat, lon)
    if r:
        return GEOFABRIK.get(r)
    return hits[0] if len(hits) == 1 else None    # Katalog fehlt: nur eindeutige Boxen


def lookup(stations):
    """stations: [(id, lat, lon)] ohne echten Preis -> ({id: {diesel, avg: True, cc, src, at}}, pending) fuer DK/SE/NO.
    pending: Laender, deren Schnitt gerade erst geladen wird – die Seite fragt diese Tankstellen dann nochmal
    (sonst blieben sie nach dem ersten Abruf nach einem Neustart fuer immer beim deutschen Profilpreis)."""
    cand = [(sid, la, lo) for sid, la, lo in stations if any(b[0] <= la <= b[1] and b[2] <= lo <= b[3] for b in BOX.values())]
    if not cand:
        return {}, set()
    avg = averages()
    out, pending = {}, set()
    for sid, la, lo in cand:
        cc = _country(la, lo)
        if cc and avg.get(cc):
            out[sid] = {"diesel": avg[cc], "avg": True, "cc": cc, "src": SHORT[cc], "open": None,
                        "at": int(_state["t"]) or int(time.time()), "name": ""}
        elif cc and _state["loading"]:
            pending.add(cc)
    return out, pending
