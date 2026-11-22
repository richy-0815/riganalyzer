"""Geofabrik-Regionen: welche Region deckt einen Streckenteil ab, wie gross ist sie,
wie lange braucht der Server zum Bauen – und die Warteschlange fuer nachbestellte Regionen.

Region-ID = Geofabrik-Pfad wie in OSM_REGIONS (z. B. europe/germany/bayern/oberbayern).
"""

import json
import re
import logging
import os
import threading
import time

import httpx
import numpy as np
import shapely
from shapely.geometry import shape

log = logging.getLogger("regions")

DATA = os.environ.get("DATA_DIR", "/data")
INDEX_URL = os.environ.get("GEOFABRIK_INDEX", "https://download.geofabrik.de/index-v1.json")
INDEX_FILE = os.path.join(DATA, "geofabrik-index.json")
EXTRA_FILE = os.path.join(DATA, "regions-extra.json")
STATS_FILE = os.path.join(DATA, "build-stats.json")
SIZES_FILE = os.path.join(DATA, "region-sizes.json")
UA = "RideAnalyzer-Prototyp/0.3 (+https://rideanalyzer.petrick.top)"

MAX_REGION_MB = float(os.environ.get("MAX_REGION_MB", "1200"))
MAX_EXTRA = int(os.environ.get("MAX_EXTRA_REGIONS", "12"))
DEFAULT_MB_PER_S = 6.5 / 60     # gemessen auf einem Raspberry Pi: ~6–7 MB PBF pro Minute
# Geofabrik-Namen sind englisch; die haeufigsten eindeutschen
DE_NAMES = {"austria": "Österreich", "slovenia": "Slowenien", "switzerland": "Schweiz", "italy": "Italien",
            "france": "Frankreich", "czech-republic": "Tschechien", "poland": "Polen", "denmark": "Dänemark",
            "netherlands": "Niederlande", "belgium": "Belgien", "luxembourg": "Luxemburg", "croatia": "Kroatien",
            "hungary": "Ungarn", "slovakia": "Slowakei", "liechtenstein": "Liechtenstein", "spain": "Spanien",
            "portugal": "Portugal", "norway": "Norwegen", "sweden": "Schweden", "germany": "Deutschland",
            "freiburg-regbez": "Schwarzwald (Reg.-Bez. Freiburg)", "brandenburg": "Berlin/Brandenburg",
            "nord-est": "Nordost-Italien", "nord-ovest": "Nordwest-Italien", "isole": "Italienische Inseln",
            "centro": "Mittelitalien", "sud": "Süditalien", "albania": "Albanien", "andorra": "Andorra",
            "belarus": "Belarus", "bosnia-herzegovina": "Bosnien und Herzegowina", "bulgaria": "Bulgarien",
            "cyprus": "Zypern", "estonia": "Estland", "faroe-islands": "Färöer", "finland": "Finnland",
            "georgia": "Georgien", "great-britain": "Großbritannien", "greece": "Griechenland",
            "guernsey-jersey": "Guernsey und Jersey", "iceland": "Island",
            "ireland-and-northern-ireland": "Irland und Nordirland", "isle-of-man": "Isle of Man",
            "kosovo": "Kosovo", "latvia": "Lettland", "lithuania": "Litauen", "macedonia": "Nordmazedonien",
            "malta": "Malta", "moldova": "Moldau", "monaco": "Monaco", "montenegro": "Montenegro",
            "romania": "Rumänien", "serbia": "Serbien", "turkey": "Türkei", "ukraine": "Ukraine",
            "praha": "Prag", "stredocesky": "Mittelböhmen"}

_lock = threading.Lock()
_index = None


def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def name_of(region):
    slug = region.rsplit("/", 1)[-1]
    if slug in DE_NAMES:
        return DE_NAMES[slug]
    idx = _load_index(download=False)
    if idx:
        for r in idx:
            if r["region"] == region:
                return re.sub(r"\s*<br\s*/?>.*", "", r["name"]).strip()
    return slug.replace("-", " ").title()


def country_of(region):
    """Landesname (deutsch) einer Region: europe/germany/hessen -> Deutschland."""
    parts = region.split("/")
    return name_of("/".join(parts[:2])) if len(parts) > 2 else name_of(region)


def by_country(regs):
    """[{name, regions:[Teilregionen]}] alphabetisch; Laender ohne Teilregionen -> regions=[]."""
    out = {}
    for r in regs:
        c = out.setdefault(country_of(r), [])
        if r.count("/") > 1:
            c.append(name_of(r))
    return [{"name": k, "regions": sorted(v)} for k, v in sorted(out.items())]


def _load_index(download=True):
    """Geofabrik-Katalog mit Umrissen (~4 MB), lokal gecacht, woechentlich erneuert."""
    global _index
    with _lock:
        if _index is not None:
            return _index
        try:
            fresh = time.time() - os.path.getmtime(INDEX_FILE) < 7 * 86400
        except OSError:
            fresh = False
        if not fresh and download:
            try:
                r = httpx.get(INDEX_URL, headers={"User-Agent": UA}, timeout=60, follow_redirects=True)
                r.raise_for_status()
                with open(INDEX_FILE + ".tmp", "wb") as f:
                    f.write(r.content)
                os.replace(INDEX_FILE + ".tmp", INDEX_FILE)
            except Exception as e:  # noqa: BLE001 – Index-Download ist Beiwerk, alter Stand bleibt
                log.warning("Geofabrik-Index nicht ladbar: %s", e)
        raw = _read(INDEX_FILE, None)
        if not raw:
            return None
        out = []
        for f in raw.get("features", []):
            p = f.get("properties", {})
            pbf = (p.get("urls") or {}).get("pbf", "")
            if not pbf.endswith("-latest.osm.pbf") or not f.get("geometry"):
                continue
            region = pbf.split("download.geofabrik.de/", 1)[-1][:-len("-latest.osm.pbf")]
            try:
                g = shape(f["geometry"])
            except Exception:  # noqa: BLE001 – kaputte Geometrie ueberspringen
                continue
            shapely.prepare(g)
            out.append({"region": region, "name": p.get("name", region), "geom": g, "area": g.area})
        out.sort(key=lambda r: r["area"])   # kleinste zuerst -> feinste passende Region
        _index = out
        return _index


def size_mb(region):
    sizes = _read(SIZES_FILE, {})
    hit = sizes.get(region)
    if hit and time.time() - hit["t"] < 7 * 86400:
        return hit["mb"]
    try:
        r = httpx.head(f"https://download.geofabrik.de/{region}-latest.osm.pbf", headers={"User-Agent": UA},
                       timeout=20, follow_redirects=True)
        mb = round(int(r.headers.get("content-length", 0)) / 1e6)
    except Exception:  # noqa: BLE001 – Groessenabfrage ist Beiwerk
        return None
    if mb:
        sizes[region] = {"mb": mb, "t": time.time()}
        _write(SIZES_FILE, sizes)
    return mb or None


def mb_per_s():
    stats = [s for s in _read(STATS_FILE, []) if s.get("mb") and s.get("sec")]
    if not stats:
        return DEFAULT_MB_PER_S
    return sum(s["mb"] for s in stats[-6:]) / sum(s["sec"] for s in stats[-6:])


def record_build(region, sec):
    mb = size_mb(region)
    if not mb:
        return
    stats = _read(STATS_FILE, [])
    stats.append({"region": region, "mb": mb, "sec": round(sec), "t": round(time.time())})
    _write(STATS_FILE, stats[-30:])


def est_seconds(region):
    mb = size_mb(region)
    return None if mb is None else mb / mb_per_s() + 120   # + Download/Start


def missing_for(lat, lon, covered_mask):
    """Strecke (lat/lon-Arrays) -> fehlende Regionen [{region, name, share}], kleinste passende je Punkt."""
    lat, lon = np.asarray(lat), np.asarray(lon)
    idx_pts = np.where(~np.asarray(covered_mask))[0]
    if len(idx_pts) == 0:
        return []
    index = _load_index()
    if not index:
        return [{"region": None, "name": "unbekannt", "share": round(len(idx_pts) / len(lat) * 100)}]
    step = max(1, len(idx_pts) // 400)
    sample = idx_pts[::step]
    found = {}
    for i in sample:
        for r in index:
            if r["area"] < 1e-6:
                continue
            if shapely.contains_xy(r["geom"], float(lon[i]), float(lat[i])):
                found[r["region"]] = found.get(r["region"], 0) + 1
                break
    total = len(sample)
    share = len(idx_pts) / len(lat)
    return [{"region": k, "name": name_of(k), "share": round(v / total * share * 100)}
            for k, v in sorted(found.items(), key=lambda kv: -kv[1])]


def extra_regions():
    return [r for r in _read(EXTRA_FILE, []) if isinstance(r, str)]


def request(region, configured, present):
    """Region nachbestellen. -> (ok, meldung)."""
    index = _load_index()
    if not index or region not in {r["region"] for r in index}:
        return False, "Unbekannte Region."
    if region in configured or region in present:
        return True, "Region ist schon vorhanden oder eingeplant."
    extra = extra_regions()
    if region in extra:
        return True, "Region ist schon in der Warteschlange."
    if len(extra) >= MAX_EXTRA:
        return False, f"Es sind schon {len(extra)} zusätzliche Regionen bestellt – bitte erst aufräumen."
    mb = size_mb(region)
    if mb is None:
        return False, "Größe der Region nicht ermittelbar – Geofabrik gerade nicht erreichbar?"
    if mb > MAX_REGION_MB:
        return False, (f"{name_of(region)} ist mit {mb} MB zu groß für den Server (Grenze {MAX_REGION_MB:.0f} MB). "
                       "Eine kleinere Teilregion wählen.")
    st = os.statvfs(DATA)
    if st.f_bavail * st.f_frsize < mb * 1e6 * 6:
        return False, "Zu wenig freier Speicher auf dem Server."
    extra.append(region)
    _write(EXTRA_FILE, extra)
    log.info("Region nachbestellt: %s (%s MB)", region, mb)
    return True, "Region ist bestellt."


def country_at(lat, lon):
    """Land (Geofabrik-Name der ersten Ebene unter europe/, z. B. "norway") fuer einen Punkt – oder None."""
    index = _load_index()   # Geofabrik-Katalog, lokal gemerkt (woechentlich neu)
    if not index:
        return None
    for r in index:
        if r["region"].count("/") == 1 and shapely.contains_xy(r["geom"], float(lon), float(lat)):
            return r["region"].split("/")[1]
    return None

