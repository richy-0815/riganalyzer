"""Strassenfoto an einem Platz (seit v0.25.0): Mapillary-Bild, das der Einfahrt am naechsten ist und moeglichst
in ihre Richtung schaut – damit man vorher sieht, ob Tor/Schranke/Zufahrt fuers Womo passen.
Schluessel nur auf dem Server (MAPILLARY_TOKEN aus dem Vault). Bilder: CC BY-SA 4.0, Quelle Mapillary."""

import logging
import math
import os
import threading
import time

import httpx

log = logging.getLogger("mapillary")

TOKEN = os.environ.get("MAPILLARY_TOKEN", "")
URL = "https://graph.mapillary.com/images"
RADIUS_M = 80
TTL = 24 * 3600          # Vorschau-Links von Mapillary laufen ab – einen Tag merken reicht
PAUSE = 900
_cache = {}
_state = {"paused_until": 0.0}
_lock = threading.Lock()
# Bilder laufen ueber den eigenen Server (/api/camps/photo/<id>), damit Meta (fbcdn) keine Nutzer-IP sieht.
# _thumbs: Bild-ID -> (bis wann gueltig, Vorschau-URL von Mapillary); nur IDs, die photo() selbst vorgeschlagen hat.
# _imgs: kleiner Speicher fertiger Bilder (je ~100-200 KB), aelteste fliegen zuerst raus.
_thumbs = {}
_imgs = {}
IMG_KEEP = 60
IMG_MAX_BYTES = 3 * 1024 * 1024
LOCAL = "/api/camps/photo/"


def _public(best):
    """Antwort fuer den Browser: Vorschau-URL durch den eigenen Pfad ersetzen, die echte URL merken."""
    if not best:
        return best
    with _lock:
        _thumbs[best["id"]] = (time.time() + TTL, best["thumb"])
        if len(_thumbs) > 5000:
            _thumbs.pop(next(iter(_thumbs)))
    return {**best, "thumb": LOCAL + best["id"]}


def _allowed(url):
    from urllib.parse import urlsplit
    u = urlsplit(url or "")
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host.endswith(".fbcdn.net") or host.endswith(".mapillary.com"))


def image(image_id):
    """(Bytes, Content-Type) des Vorschaubilds oder None. Holt nur URLs, die Mapillary selbst geliefert hat."""
    with _lock:
        hit = _imgs.get(image_id)
        src = _thumbs.get(image_id)
    if hit:
        return hit
    if not src or src[0] < time.time() or not _allowed(src[1]):
        return None
    try:
        r = httpx.get(src[1], timeout=10, follow_redirects=False)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("Mapillary-Bild %s: %s", image_id, type(e).__name__)
        return None
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype not in ("image/jpeg", "image/png", "image/webp") or len(r.content) > IMG_MAX_BYTES:
        return None
    out = (r.content, ctype)
    with _lock:
        _imgs[image_id] = out
        while len(_imgs) > IMG_KEEP:
            _imgs.pop(next(iter(_imgs)))
    return out


def _dist(la1, lo1, la2, lo2):
    k = math.pi / 180
    x = (lo2 - lo1) * k * math.cos((la1 + la2) * k / 2)
    return 6371000 * math.hypot(x, (la2 - la1) * k)


def _bearing(la1, lo1, la2, lo2):
    k = math.pi / 180
    y = math.sin((lo2 - lo1) * k) * math.cos(la2 * k)
    x = math.cos(la1 * k) * math.sin(la2 * k) - math.sin(la1 * k) * math.cos(la2 * k) * math.cos((lo2 - lo1) * k)
    return (math.atan2(y, x) / k + 360) % 360


def pick(images, lat, lon):
    """Bestes Bild: nah und Blickrichtung zum Platz (Abweichung kostet wie 1 m je 3 Grad). None, wenn nichts passt."""
    best, score = None, None
    for im in images:
        g = (im.get("computed_geometry") or im.get("geometry") or {}).get("coordinates")
        if not g or not im.get("thumb_1024_url"):
            continue
        ilon, ilat = g[0], g[1]
        d = _dist(lat, lon, ilat, ilon)
        if d > RADIUS_M:
            continue
        ang = im.get("computed_compass_angle", im.get("compass_angle"))
        off = 90.0 if ang is None or d < 5 else abs((_bearing(ilat, ilon, lat, lon) - ang + 180) % 360 - 180)
        s = d + off / 3
        if score is None or s < score:
            best, score = {"id": im["id"], "thumb": im["thumb_1024_url"], "at": int((im.get("captured_at") or 0) / 1000),
                           "dist": round(d), "looks": off <= 60, "pano": bool(im.get("is_pano"))}, s
    if best:
        best["link"] = f"https://www.mapillary.com/app/?pKey={best['id']}&focus=photo"
    return best


def photo(lat, lon):
    """{"enabled", "photo": {...}|None, "credit"}"""
    if not TOKEN:
        return {"enabled": False, "photo": None}
    key = (round(lat, 5), round(lon, 5))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return {"enabled": True, "photo": _public(hit[1]), "credit": "Foto: Mapillary (CC BY-SA 4.0)"}
    if time.time() < _state["paused_until"]:
        return {"enabled": True, "photo": None}
    d = RADIUS_M / 111000
    try:
        r = httpx.get(URL, params={"access_token": TOKEN, "limit": 50,
                                   "fields": "id,thumb_1024_url,captured_at,compass_angle,computed_compass_angle,computed_geometry,is_pano",
                                   "bbox": f"{lon - d * 1.6},{lat - d},{lon + d * 1.6},{lat + d}"}, timeout=10)
        r.raise_for_status()
        best = pick(r.json().get("data", []), lat, lon)
    except (httpx.HTTPError, ValueError) as e:
        _state["paused_until"] = time.time() + PAUSE
        log.warning("Mapillary: %s – 15 min Pause", type(e).__name__)
        return {"enabled": True, "photo": None}
    with _lock:
        _cache[key] = (time.time(), best)
        if len(_cache) > 5000:
            _cache.pop(next(iter(_cache)))
    return {"enabled": True, "photo": _public(best), "credit": "Foto: Mapillary (CC BY-SA 4.0)"}
