"""Echte Dieselpreise in Daenemark (seit v0.32.0).

Seit BEK 1351/2025 § 16a muessen groessere Tankstellen-Betreiber ihre Preise je Station ueber eine oeffentliche
Schnittstelle anbieten (Vorgaben der Konkurrence- og Forbrugerstyrelsen: REST/JSON, DKK, Zeit in UTC).
Ein zentrales Verzeichnis gibt es nicht – jede Kette hat ihre eigene. Eingebaut sind die, die ohne Schluessel
und mit Koordinaten auskommen; der Rest steht in KETTEN (Stand, was fuer den Einbau noch fehlt).
Zuordnung wie bei FR/ES/IT: OSM-Tankstelle -> naechste Station der Kette <= 150 m.
"""

import logging
import threading
import time
from datetime import datetime

import httpx

import fuelprice_eu
import fuelprice_nordic

log = logging.getLogger("fuelprice")

UA = {"User-Agent": "RigAnalyzer/1 (riganalyzer.petrick.top; Tankplan)", "Accept": "application/json"}
TTL = 900
PAUSE = 900
BOX = (54.4, 57.9, 7.8, 15.3)

# Stand 02.11.2026 – was die Ketten anbieten (Quelle: Dokumentation auf den Seiten der Ketten)
KETTEN = {
    "OK": {"status": "eingebaut", "url": "https://mobility-prices.ok.dk/api/v1/fuel-prices",
           "doc": "https://www.ok.dk/globalassets/pdfer/diverse/offentlig-api-dokumentation.pdf",
           "note": "ohne Schluessel, alle Stationen mit Koordinaten in einem Abruf"},
    "Circle K / INGO": {"status": "vorbereitet", "url": "https://api.circlek.com/eu/prices/v1/fuel/countries/DK",
                        "doc": "https://www.ingo.dk/vores-lave-priser/br%C3%A6ndstofpriser/api",
                        "note": "Header X-App-Name: PRICES; Liste ohne Koordinaten -> Abgleich ueber Adresse/OSM noetig"},
    "Uno-X": {"status": "Zugang anfragen", "url": None,
              "doc": "https://unoxmobility.dk/privat/braendstofpriser",
              "note": "Zugang per Mail an info@unox.dk (Doku-PDF auf der Seite)"},
    "Q8 / F24": {"status": "offen", "url": None, "doc": None, "note": "Doku noch nicht gefunden"},
    "Shell": {"status": "offen", "url": None, "doc": None, "note": "Doku noch nicht gefunden"},
    "OIL! / Go'on": {"status": "offen", "url": None, "doc": None, "note": "Doku noch nicht gefunden"},
}
SOURCE = "Preis-APIs der Tankstellen (Dänemark, BEK 1351/2025): OK"

_state = {"t": 0.0, "grid": None, "paused": 0.0, "loading": False}
_lock = threading.Lock()


def parse_ok(data, dkk_per_eur):
    """OK-Antwort -> [{lat, lon, diesel (€/l), at, name}] – Diesel = „Diesel“ ohne HVO/Premium, sonst der billigste."""
    out = []
    for it in (data or {}).get("items", []):
        c = it.get("coordinates") or {}
        la, lo = c.get("latitude"), c.get("longitude")
        ds = [(p.get("product_name") or "", p.get("price")) for p in it.get("prices", [])
              if "diesel" in (p.get("product_name") or "").lower() and isinstance(p.get("price"), (int, float))]
        plain = [x for x in ds if not any(w in x[0].lower() for w in ("hvo", "premium", "+", "truck", "lastbil"))]
        pick = min(plain or ds, key=lambda x: x[1], default=None)
        if la is None or lo is None or not pick:
            continue
        eur = pick[1] / dkk_per_eur
        if not 0.8 < eur < 4:
            continue
        try:
            at = int(datetime.fromisoformat(str(it.get("last_updated_time")).replace("Z", "+00:00")).timestamp())
        except ValueError:
            at = None
        out.append({"lat": float(la), "lon": float(lo), "diesel": round(eur, 3), "at": at,
                    "name": " ".join(str(v) for v in ("OK", it.get("street"), it.get("city")) if v)})
    return out


def _load():
    try:
        rate = fuelprice_nordic.fx().get("DKK")
        if not rate:
            raise ValueError("kein DKK-Kurs")
        r = httpx.get(KETTEN["OK"]["url"], headers=UA, timeout=httpx.Timeout(30, connect=10))
        r.raise_for_status()
        _state.update(t=time.time(), grid=fuelprice_eu.Grid(parse_ok(r.json(), rate)))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        _state["paused"] = time.time() + PAUSE
        log.warning("Dieselpreise DK (OK): %s – %d min Pause", type(e).__name__, PAUSE // 60)
    finally:
        _state["loading"] = False


BACKGROUND = True


def lookup(stations):
    """stations: [(id, lat, lon)] -> ({id: {diesel, open, name, at, src, cc}}, pending)"""
    pts = [s for s in stations if BOX[0] <= s[1] <= BOX[1] and BOX[2] <= s[2] <= BOX[3]]
    if not pts or time.time() < _state["paused"]:
        return {}, False
    with _lock:
        fresh = _state["grid"] is not None and time.time() - _state["t"] < TTL
        start = not fresh and not _state["loading"]
        if start:
            _state["loading"] = True
    if start:
        if BACKGROUND:
            threading.Thread(target=_load, name="fuel-DK", daemon=True).start()
        else:
            _load()
    g = _state["grid"]
    if g is None:
        return {}, bool(_state["loading"])
    out = {}
    for sid, la, lo in pts:
        if (s := g.near(la, lo)):
            out[sid] = {"diesel": s["diesel"], "open": None, "name": s["name"], "at": s.get("at") or int(time.time()),
                        "src": "OK", "cc": "DK"}
    return out, False
