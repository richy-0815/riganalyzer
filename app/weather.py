"""Wetter bei der Planung (seit v0.13): Vorhersage je Etappe (Open-Meteo), Boeen fuers Gespann, spaeter Warnungen.

- Ein Open-Meteo-Aufruf fuer alle Stuetzpunkte (gerundet auf 0,05°), Stunde = Stunde der geplanten Ankunft (UTC).
- Cache je gerundetem Punkt 30 min; Etappen jenseits des Horizonts (16 Tage) bekommen nur „ab TT.MM. verfuegbar“.
- Faellt ein Dienst aus, kommt der Rest trotzdem (errors je Teil).
"""

import datetime as dt
import logging
import re
import threading
import time

import httpx

log = logging.getLogger("weather")

OM_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = "temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m,weather_code"
FORECAST_DAYS = 16
CACHE_TTL = 1800
MAX_POINTS = 40
UA = {"User-Agent": "RigAnalyzer/0.13 (+https://riganalyzer.petrick.top)"}
ATTRIBUTION = "Wetter: Open-Meteo · Radar: RainViewer · Warnungen: DWD, MeteoAlarm"

_cache = {}   # (lat, lon) gerundet -> (zeit, {iso-stunde: (temp, mm, wind, boee, code)})
_lock = threading.Lock()

WMO = {0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bedeckt", 45: "Nebel", 48: "Reifnebel",
       51: "leichter Niesel", 53: "Niesel", 55: "starker Niesel", 56: "gefrierender Niesel", 57: "gefrierender Niesel",
       61: "leichter Regen", 63: "Regen", 65: "starker Regen", 66: "gefrierender Regen", 67: "gefrierender Regen",
       71: "leichter Schnee", 73: "Schnee", 75: "starker Schnee", 77: "Schneegriesel",
       80: "Regenschauer", 81: "Regenschauer", 82: "heftige Schauer", 85: "Schneeschauer", 86: "Schneeschauer",
       95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Gewitter mit Hagel"}


def _now():
    return dt.datetime.now(dt.timezone.utc)


def wmo_text(code):
    return WMO.get(int(code) if code is not None else -1, "")


def gust_level(gust, rig, moto=False):
    """Boeen fuers Gespann: <50 nichts, 50–69 knapp, >=70 kritisch; ohne Anhaenger ab 70 nur Hinweis;
    Motorrad (RideAnalyzer, seit 0.15) Hinweis schon ab 60."""
    if gust is None:
        return None
    if moto:
        return "info" if gust >= 60 else None
    if not rig:
        return "info" if gust >= 70 else None
    return "critical" if gust >= 70 else "tight" if gust >= 50 else None


def _key(lat, lon):
    return (round(round(lat / 0.05) * 0.05, 2), round(round(lon / 0.05) * 0.05, 2))


def _fetch(keys):
    """Open-Meteo fuer mehrere Orte in einem Aufruf -> {key: {stunde: werte}}."""
    r = httpx.get(OM_URL, params={"latitude": ",".join(str(k[0]) for k in keys),
                                  "longitude": ",".join(str(k[1]) for k in keys),
                                  "hourly": HOURLY, "timezone": "UTC", "forecast_days": FORECAST_DAYS},
                  headers=UA, timeout=12)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = [data]
    out = {}
    for k, d in zip(keys, data):
        h = d["hourly"]
        out[k] = {t: (h["temperature_2m"][i], h["precipitation"][i], h["wind_speed_10m"][i],
                      h["wind_gusts_10m"][i], h["weather_code"][i]) for i, t in enumerate(h["time"])}
    return out


def forecast_points(points, now=None):
    """points: [(lat, lon, eta_utc)] -> je Punkt Werte zur ETA-Stunde, {"later": ...} oder None (Dienst aus)."""
    now = now or _now()
    last_day = now.date() + dt.timedelta(days=FORECAST_DAYS - 1)
    res = [None] * len(points)
    need = []
    with _lock:
        for i, (lat, lon, eta) in enumerate(points):
            if eta.date() > last_day:
                res[i] = {"later": "ab " + (eta.date() - dt.timedelta(days=FORECAST_DAYS - 1)).strftime("%d.%m.") + " verfügbar"}
                continue
            k = _key(lat, lon)
            hit = _cache.get(k)
            if (not hit or time.time() - hit[0] > CACHE_TTL) and k not in need:
                need.append(k)
    if need:
        fresh = _fetch(need)   # Fehler gehen an den Aufrufer (Teilantwort)
        with _lock:
            for k, v in fresh.items():
                _cache[k] = (time.time(), v)
            if len(_cache) > 5000:
                _cache.clear()
    with _lock:
        for i, (lat, lon, eta) in enumerate(points):
            if res[i] is not None:
                continue
            hours = (_cache.get(_key(lat, lon)) or (0, {}))[1]
            v = hours.get(eta.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:00"))
            if v:
                res[i] = {"temp": v[0], "precip_mm": v[1], "wind": v[2], "gust": v[3], "code": v[4], "text": wmo_text(v[4])}
    return res


def _parse_pts(body):
    stages = body.get("stages") if isinstance(body, dict) else None
    if not isinstance(stages, list) or not stages:
        raise ValueError("stages fehlt")
    total = sum(len(s.get("pts") or []) for s in stages)
    if total > MAX_POINTS:
        raise ValueError(f"höchstens {MAX_POINTS} Punkte")
    out = []
    for s in stages:
        pts = []
        for p in s.get("pts") or []:
            lat, lon, iso = float(p[0]), float(p[1]), str(p[2])
            km = float(p[3]) if len(p) > 3 and p[3] is not None else None
            eta = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if eta.tzinfo is None:
                eta = eta.replace(tzinfo=dt.timezone.utc)
            pts.append((lat, lon, eta, km))
        out.append((int(s.get("n", len(out) + 1)), pts))
    return out


def route_weather(body):
    """{"stages":[{"n","pts":[[lat,lon,iso,km?],...]}], "rig": bool, "moto": bool} -> Wetter je Etappe (ValueError bei falscher Eingabe)."""
    stages = _parse_pts(body)
    rig = bool(body.get("rig", True))
    moto = bool(body.get("moto"))
    errors = {}
    flat = [(lat, lon, eta) for _, pts in stages for lat, lon, eta, _ in pts]
    try:
        vals = forecast_points(flat)
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("Open-Meteo: %s", e)
        errors["forecast"] = "Wetter gerade nicht abrufbar"
        vals = [None] * len(flat)
    out, j = [], 0
    for n, pts in stages:
        mine = vals[j:j + len(pts)]
        j += len(pts)
        known = [(v, p) for v, p in zip(mine, pts) if v and "temp" in v]
        worst = None
        if known:
            pv, pp = max(known, key=lambda x: x[0]["precip_mm"] or 0)
            gv, gp = max(known, key=lambda x: x[0]["gust"] or 0)
            worst = {"precip_mm": pv["precip_mm"], "precip_at": pp[3], "gust": gv["gust"], "gust_at": gp[3],
                     "gust_level": gust_level(gv["gust"], rig, moto)}
        end = mine[-1] if mine else None
        out.append({"n": n, "end": end if end and "temp" in end else None, "worst": worst,
                    "later": end.get("later") if end and "later" in end else None})
    try:
        warns, werr = warnings_for(stages)
        errors.update(werr)
    except Exception as e:   # noqa: BLE001 – Warnungen sind Zusatz – nie die ganze Antwort verlieren
        log.warning("Warnungen: %s", e)
        warns, errors["warnings"] = {}, "Warnungen gerade nicht abrufbar"
    for st in out:
        st["warnings"] = warns.get(str(st["n"]), [])
    return {"stages": out, "attribution": ATTRIBUTION, "errors": errors}


# ---------------------------------------------------------------- Unwetterwarnungen (DWD, MeteoAlarm)
DWD_WFS = "https://maps.dwd.de/geoserver/dwd/ows"
MA_FEED = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-{}"
FEED_TTL = 600
FAIL_TTL = 120          # Ausfall kurz merken – sonst wartet jede Anfrage erneut auf den Timeout
FEED_MAX = 64
FEED_TIMEOUT = 8
DWD_LEVEL = {"Minor": "yellow", "Moderate": "orange", "Severe": "red", "Extreme": "red"}
MA_TYPE = {"1": "Wind", "2": "Schnee/Glätte", "3": "Gewitter", "4": "Nebel", "5": "Hitze", "6": "Kälte", "7": "Küste",
           "8": "Waldbrand", "9": "Lawinen", "10": "Regen", "12": "Hochwasser", "13": "Regen/Hochwasser"}
LEVEL_ORDER = {"red": 0, "orange": 1, "yellow": 2}
# oertliche Gefahren: nur mit Gebietsgrenzen (DE, FR) sinnvoll, sonst landet z. B. jede UK-Kuestenmeldung an jeder Etappe
LOCAL_ONLY = {"Küste", "Hochwasser", "Regen/Hochwasser", "Lawinen", "Waldbrand"}
# Geofabrik-Laender -> MeteoAlarm-Feed (Sammel-Auszuege wie alps/dach fehlen bewusst)
FEED_OF = {c: c for c in ("germany", "france", "spain", "italy", "austria", "switzerland", "netherlands", "belgium",
                          "luxembourg", "poland", "denmark", "portugal", "slovenia", "croatia", "hungary", "slovakia",
                          "norway", "sweden", "finland", "greece", "romania", "bulgaria", "serbia", "estonia", "latvia",
                          "lithuania", "malta", "cyprus", "iceland", "montenegro", "bosnia-herzegovina", "moldova")}
FEED_OF.update({"czech-republic": "czechia", "great-britain": "united-kingdom", "ireland-and-northern-ireland": "ireland"})
_feed_cache = {}
_nuts = None
_area_raw = None
_area_geoms = {}


class _NoFeed(Exception):
    pass


def _get_json(url, params=None):
    r = httpx.get(url, params=params, headers=UA, timeout=FEED_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _store(key, value):
    now = time.time()
    with _lock:
        for k in [k for k, (t, _) in _feed_cache.items() if now - t > FEED_TTL]:
            del _feed_cache[k]
        if len(_feed_cache) >= FEED_MAX:
            del _feed_cache[min(_feed_cache, key=lambda k: _feed_cache[k][0])]
        _feed_cache[key] = (now, value)


def _cached(key, url, params=None):
    with _lock:
        hit = _feed_cache.get(key)
    if hit:
        age, val = time.time() - hit[0], hit[1]
        if isinstance(val, Exception) and age < FAIL_TTL:
            raise val
        if not isinstance(val, Exception) and age < FEED_TTL:
            return val
    try:
        data = _get_json(url, params)
    except httpx.HTTPStatusError as e:
        err = _NoFeed() if e.response is not None and e.response.status_code == 404 else e
        _store(key, err)
        raise err  # noqa: B904 – err ist e selbst oder _NoFeed; e bleibt als Kontext
    except (httpx.HTTPError, ValueError) as e:
        _store(key, e)
        raise
    _store(key, data)
    return data


def _country_of(lat, lon):
    """MeteoAlarm-Feed des Landes eines Punkts aus dem lokal gecachten Geofabrik-Katalog (Sammel-Auszuege ignoriert)."""
    import shapely
    import regions
    for r in regions._load_index(download=False) or []:
        name = r["region"].split("/", 1)[1] if r["region"].count("/") == 1 else None
        if name in FEED_OF and shapely.contains_xy(r["geom"], lon, lat):
            return FEED_OF[name]
    return None


def _areas():
    """Grenzen der Warngebiete aus geo/meteoalarm_areas.json (tools/meteoalarm-areas.py), Schluessel „EMMA_ID:ES159“,
    „NUTS3:HR032“, „NUTS2:HU22“ – Geometrien erst bei Bedarf gebaut."""
    global _area_raw
    if _area_raw is None:
        import json
        import os
        with open(os.path.join(os.path.dirname(__file__), "geo", "meteoalarm_areas.json"), encoding="utf-8") as f:
            _area_raw = json.load(f)["areas"]
    return _area_raw


def _area_geom(key):
    from shapely.geometry import shape
    if key not in _area_geoms:
        g = _areas().get(key)
        _area_geoms[key] = shape(g) if g else None
    return _area_geoms[key]


def _cap_polygons(a):
    """CAP-Polygone eines Gebiets (CH, SE, NO, UK, NL, SI …): „lat,lon lat,lon …“, im Feed teils als Liste in Textform."""
    from shapely.geometry import Polygon
    raw = a.get("polygon")
    items = raw if isinstance(raw, list) else re.findall(r"[-\d.,\s]{20,}", str(raw or ""))
    out = []
    for it in items:
        try:
            pts = [(float(lo), float(la)) for la, lo in (p.split(",") for p in str(it).split())]
        except ValueError:
            continue
        if len(pts) >= 3:
            pg = Polygon(pts)
            out.append(pg if pg.is_valid else pg.buffer(0))
    return out


def area_shapes(feed, a):
    """Geometrien eines Warngebiets oder None, wenn keine Grenzen bekannt sind (dann gilt die Land-Regel)."""
    if a.get("polygon"):
        pg = _cap_polygons(a)
        if pg:
            return pg
    out = []
    for g in a.get("geocode") or []:
        typ, val = g.get("valueName"), g.get("value")
        if not val:
            continue
        if feed == "france" and typ == "NUTS3":
            sh = _nuts_fr().get(val)
        elif typ in ("EMMA_ID", "NUTS2", "NUTS3"):
            sh = _area_geom(f"{typ}:{val}")
        else:
            sh = None
        if sh is not None:
            out.append(sh)
    return out or None


def _nuts_fr():
    global _nuts
    if _nuts is None:
        import json
        import os
        from shapely.geometry import shape
        with open(os.path.join(os.path.dirname(__file__), "geo", "nuts3_fr.json"), encoding="utf-8") as f:
            _nuts = {k: shape(v["geometry"]) for k, v in json.load(f)["regions"].items()}
    return _nuts


def _parse_t(s):
    t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _overlaps(onset, expires, t0, t1):
    try:
        a, b = _parse_t(onset), _parse_t(expires)
    except (ValueError, TypeError):
        return True
    return a <= t1 and b >= t0


def _stage_geom(pts):
    from shapely.geometry import LineString, Point
    xy = [(lon, lat) for lat, lon, _, _ in pts]
    return LineString(xy) if len(xy) > 1 else Point(xy[0]).buffer(0.02)


def _ma_info(alert):
    infos = alert.get("info") or []
    pick = next((i for i in infos if str(i.get("language", "")).startswith("de")), None) or \
        next((i for i in infos if str(i.get("language", "")).startswith("en")), None) or (infos[0] if infos else {})
    par = {p.get("valueName"): p.get("value", "") for p in pick.get("parameter") or []}
    lvl = (par.get("awareness_level", "").split(";") + ["", ""])[1].strip().lower()   # UK: „Orange“
    typ = (par.get("awareness_type", "").split(";") + [""])[0].strip()
    return pick, lvl, MA_TYPE.get(typ) or pick.get("event", "")


def _when(s, default):
    try:
        return _parse_t(s)
    except (ValueError, TypeError):
        return default


def _group(rows):
    """Eine Zeile je (Stufe, Ereignis, Quelle) – Zeitraum von fruehestem Beginn bis spaetestem Ende,
    Gebiete zusammengefasst („A, B, C +2“)."""
    lo, hi = dt.datetime.min.replace(tzinfo=dt.timezone.utc), dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    out = {}
    for r in rows:
        k = (r["level"], r["event"], r["source"])
        g = out.setdefault(k, {**r, "_areas": []})
        if _when(r["from"], hi) < _when(g["from"], hi):
            g["from"] = r["from"]
        if _when(r["to"], lo) > _when(g["to"], lo):
            g["to"] = r["to"]
        if r["area"] and r["area"] not in g["_areas"]:
            g["_areas"].append(r["area"])
    res = []
    for g in out.values():
        a = g.pop("_areas")
        g["area"] = ", ".join(a[:3]) + (f" +{len(a) - 3}" if len(a) > 3 else "")
        res.append(g)
    return sorted(res, key=lambda x: (LEVEL_ORDER.get(x["level"], 9), x["from"] or ""))


def _dwd_rows(stages, de_pts, geoms, window):
    from shapely.geometry import shape
    rows = {}
    for n, pts in de_pts.items():   # Box je Etappe nur ueber die deutschen Punkte
        bb = (min(p[1] for p in pts) - 0.2, min(p[0] for p in pts) - 0.2, max(p[1] for p in pts) + 0.2, max(p[0] for p in pts) + 0.2)
        data = _cached(("dwd", tuple(round(x, 1) for x in bb)), DWD_WFS, {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeName": "dwd:Warnungen_Landkreise",
            "outputFormat": "application/json", "bbox": f"{bb[0]},{bb[1]},{bb[2]},{bb[3]},EPSG:4326"})   # DWD: Laenge,Breite!
        for f in data.get("features", []):
            p = f.get("properties") or {}
            lvl = DWD_LEVEL.get(p.get("SEVERITY"))
            if lvl and f.get("geometry") and _overlaps(p.get("ONSET"), p.get("EXPIRES"), *window[n]) \
                    and shape(f["geometry"]).intersects(geoms[n]):
                rows.setdefault(n, []).append({"level": lvl, "event": str(p.get("EVENT", "")).capitalize(), "from": p.get("ONSET"),
                                               "to": p.get("EXPIRES"), "area": p.get("AREADESC", ""), "source": "DWD"})
    return rows


def _ma_rows(feed, stage_ns, geoms, window):
    data = _cached(("ma", feed), MA_FEED.format(feed))
    rows = {}
    for w in data.get("warnings", []):
        alert = w.get("alert") or {}
        if str(alert.get("msgType", "")).lower() == "cancel":
            continue
        info, lvl, event = _ma_info(alert)
        if lvl not in LEVEL_ORDER:
            continue
        for a in info.get("area") or []:
            shapes = area_shapes(feed, a)   # Grenzen fuer (fast) alle Laender, nicht nur Frankreich
            for n in stage_ns:
                if not _overlaps(info.get("onset"), info.get("expires"), *window[n]):
                    continue
                if shapes is not None:
                    hit = any(sh.intersects(geoms[n]) for sh in shapes)
                else:   # ohne Grenzen: nur grossraeumiges Wetter, nur orange/rot je Land
                    hit = lvl in ("orange", "red") and event not in LOCAL_ONLY
                if hit:
                    rows.setdefault(n, []).append({"level": lvl, "event": event, "from": info.get("onset"),
                                                   "to": info.get("expires"), "area": a.get("areaDesc", ""), "source": "MeteoAlarm"})
    return rows


def warnings_for(stages):
    """stages: [(n, [(lat, lon, eta, km)])] -> ({"n": [warnung]}, errors). Dienste parallel, je hoechstens ~8 s."""
    from concurrent.futures import ThreadPoolExecutor
    out = {str(n): [] for n, _ in stages}
    errors = {}
    feeds, de_pts = {}, {}
    for n, pts in stages:
        for lat, lon, eta, km in pts:
            c = _country_of(lat, lon)
            if c == "germany":
                de_pts.setdefault(str(n), []).append((lat, lon, eta, km))
            elif c:
                feeds.setdefault(c, set()).add(str(n))
    geoms = {str(n): _stage_geom(pts) for n, pts in stages if pts}
    window = {str(n): (pts[0][2] - dt.timedelta(hours=1), pts[-1][2] + dt.timedelta(hours=1)) for n, pts in stages if pts}
    jobs = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        if de_pts:
            jobs["dwd"] = ex.submit(_dwd_rows, stages, de_pts, geoms, window)
        for feed, ns in feeds.items():
            jobs[f"meteoalarm-{feed}"] = ex.submit(_ma_rows, feed, ns, geoms, window)
        for name, fut in jobs.items():
            try:
                for n, rows in fut.result().items():
                    out[n] += rows
            except _NoFeed:
                pass   # Land ohne MeteoAlarm-Feed
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("Warnungen %s: %s", name, e)
                errors[name] = ("DWD-Warnungen" if name == "dwd" else "Warnungen") + " gerade nicht abrufbar"
    return {n: _group(rows) for n, rows in out.items()}, errors


# ---------------------------------------------------------------- Regenradar (RainViewer, Kacheln direkt vom Browser/Handy)
RV_INDEX = "https://api.rainviewer.com/public/weather-maps.json"


def radar_latest():
    """Letztes Radarbild: {host, path, time, tiles} – Index 5 min zwischengespeichert (Teil von FEED-Cache)."""
    with _lock:
        hit = _feed_cache.get("rainviewer")
        if hit and time.time() - hit[0] < 300:
            return hit[1]
    d = _get_json(RV_INDEX)
    last = (d.get("radar") or {}).get("past", [])[-1]
    res = {"host": d["host"], "path": last["path"], "time": last["time"],
           "tiles": f'{d["host"]}{last["path"]}/256/{{z}}/{{x}}/{{y}}/2/1_1.png'}
    with _lock:
        _feed_cache["rainviewer"] = (time.time(), res)
    return res
