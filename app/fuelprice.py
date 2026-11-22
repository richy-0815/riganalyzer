"""Echte Dieselpreise fuer den Tankplan (seit v0.17.0): Tankerkönig (Markttransparenzstelle, nur Deutschland).
Seit 0.23.0 schonend: Die Stationen eines Gebiets (list.php, Umkreis) bleiben 24 h gemerkt – Tankstellen ziehen
nicht um. Neue Preise holt prices.php fuer bis zu 10 bekannte Stationen je Abruf. Jede OSM-Tankstelle bekommt
die naechste TK-Station (<= 150 m, eindeutig bis 400 m).
Der Schluessel bleibt auf dem Server (Umgebung TANKERKOENIG_KEY, aus dem Ansible-Vault)."""

import logging
import math
import os
import threading
import time

import httpx

log = logging.getLogger("fuelprice")

KEY = os.environ.get("TANKERKOENIG_KEY", "")
URL = "https://creativecommons.tankerkoenig.de/json/list.php"
PRICES_URL = "https://creativecommons.tankerkoenig.de/json/prices.php"
SOURCE = "Tankerkönig (Markttransparenzstelle, CC BY 4.0)"
RAD_KM = 5            # Umkreis je Gebietsabruf
COVER_M = 4000        # Stationen bis hier deckt ein Gebietsabruf ab
MATCH_M = 150         # OSM-Tankstelle <-> TK-Station
MATCH_FAR_M = 400     # weiter weg nur, wenn eindeutig (grosse Gelaende wie Globus/Autohof: OSM-Punkt != Zapfsaeule)
MAX_CALLS = 8         # je Anfrage (Gebiete + Preisabrufe zusammen) – Tankerkönig sperrt bei Dauerfeuer
MIN_GAP = 1.5         # Sekunden zwischen zwei Abrufen, ueber alle Nutzer
PAUSE = 900           # nach Fehler/Sperre 15 min gar nicht mehr anklopfen
LIST_TTL = 24 * 3600  # Stationen eines Gebiets
TTL = 600             # Preis einer Station
FRESH_TTL = 120       # „Jetzt prüfen“: Preise nur bis 2 min alt nehmen
IDS_PER_CALL = 10     # prices.php nimmt hoechstens 10 Stationen
DE = (47.27, 55.06, 5.87, 15.04)   # grob Deutschland: lat min/max, lon min/max
_cache = {}           # (lat, lon) gerundet -> (Zeit, [TK-Stationen]); jede Station traegt _t = Stand ihres Preises
_state = {"last": 0.0, "paused_until": 0.0}
_gate = threading.Lock()


def _dist(la1, lo1, la2, lo2):
    k = math.pi / 180
    x = (lo2 - lo1) * k * math.cos((la1 + la2) * k / 2)
    return 6371000 * math.hypot(x, (la2 - la1) * k)


def _in_de(lat, lon):
    return DE[0] <= lat <= DE[1] and DE[2] <= lon <= DE[3]


def _get(url, params):
    with _gate:
        wait = _state["last"] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _state["last"] = time.time()
        r = httpx.get(url, params={**params, "apikey": KEY}, timeout=8)
    r.raise_for_status()
    return r.json()


def _around(lat, lon):
    """Stationen im Umkreis – aus dem Gebietsspeicher oder per list.php. -> (Stationen, ob ein Abruf noetig war)"""
    key = (round(lat, 2), round(lon, 2))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < LIST_TTL:
        return hit[1], False
    data = _get(URL, {"lat": key[0], "lng": key[1], "rad": RAD_KM, "sort": "dist", "type": "diesel"})
    stations = data.get("stations", []) if data.get("ok") else []
    now = time.time()
    for t in stations:
        t["_t"] = now
    _cache[key] = (now, stations)
    if len(_cache) > 500:
        _cache.pop(next(iter(_cache)))
    return stations, True


def _refresh(stations):
    """Neue Preise fuer bekannte Stationen (prices.php, je Abruf bis zu 10). Geschlossen: alter Preis bleibt."""
    data = _get(PRICES_URL, {"ids": ",".join(t["id"] for t in stations)})
    got = data.get("prices", {}) if data.get("ok") else {}
    now = time.time()
    for t in stations:
        p = got.get(t["id"])
        if not p:
            continue
        t["_t"] = now
        t["isOpen"] = p.get("status") == "open"
        d = p.get("diesel")
        if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0:
            t["price"] = float(d)
            if "diesel" in t:
                t["diesel"] = float(d)


def _price(t):
    """Dieselpreis einer TK-Station: bei type=diesel heisst das Feld „price“, bei type=all „diesel“ (QA 31.10.:
    bisher wurde nur „diesel“ gelesen – mit type=diesel kam so nie ein Preis an)."""
    for k in ("diesel", "price"):
        v = t.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0.3 < v < 5:
            return float(v)
    return None


def _match(tk, lat, lon):
    found = sorted(((_dist(lat, lon, t["lat"], t["lng"]), t) for t in tk if _price(t) is not None), key=lambda x: x[0])
    if not found or found[0][0] > MATCH_FAR_M:
        return None
    d0, best = found[0]
    if d0 > MATCH_M and len(found) > 1 and found[1][0] - d0 < 150:
        return None
    return best


def prices(stations, fresh=False):
    """stations: [(osm_id, lat, lon)] -> {osm_id: {"diesel", "open", "name", "at"}} (nur gefundene, nur DE)."""
    if not KEY or time.time() < _state["paused_until"]:
        return {}
    todo = [s for s in stations if _in_de(s[1], s[2])]
    matched, calls = {}, 0
    try:
        while todo:
            _, clat, clon = todo[0]
            key = (round(clat, 2), round(clon, 2))
            if calls >= MAX_CALLS and not (key in _cache and time.time() - _cache[key][0] < LIST_TTL):
                break
            tk, called = _around(clat, clon)
            calls += called
            rest = []
            for sid, lat, lon in todo:
                if _dist(clat, clon, lat, lon) > COVER_M:
                    rest.append((sid, lat, lon))
                elif (near := _match(tk, lat, lon)):
                    matched[sid] = near
            todo = rest
        ttl = FRESH_TTL if fresh else TTL
        stale = list({id(t): t for t in matched.values() if time.time() - t.get("_t", 0) >= ttl}.values())
        for i in range(0, len(stale), IDS_PER_CALL):
            if calls >= MAX_CALLS:
                break
            _refresh(stale[i:i + IDS_PER_CALL])
            calls += 1
    except (httpx.HTTPError, ValueError) as e:
        _state["paused_until"] = time.time() + PAUSE
        log.warning("Tankerkönig: %s – 15 min Pause", type(e).__name__)
    out = {}
    for sid, t in matched.items():
        p = _price(t)
        if p is not None:
            out[sid] = {"diesel": p, "open": bool(t.get("isOpen")), "name": t.get("name", ""),
                        "at": int(t.get("_t") or time.time())}
    return out
