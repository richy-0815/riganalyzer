"""Luftbild-Kacheln fuer die Planer-Karte ueber den eigenen Server (seit 0.52.1).

Vorher lud der Browser die Kacheln direkt bei Esri (USA) – mit der IP der Nutzer. Jetzt holt sie der Server; Esri
sieht nur ihn. Nur gueltige Kacheln (z 0..19, x/y im Raster), feste Quelle, kleiner Speicher im RAM, Bremse je Login.
"""
import logging
import threading
import time

import httpx

log = logging.getLogger("luftbild")

URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
MAX_Z = 19
KEEP = 400            # Kacheln im Speicher (je ~20-60 KB)
PER_MIN = 240         # je Login und Minute – die Mini-Karte braucht beim Zoomen ein paar Dutzend
_tiles = {}
_hits = {}
_lock = threading.Lock()


class TooMany(Exception):
    pass


def _check(z, x, y):
    if not (0 <= z <= MAX_Z and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise ValueError("Keine gültige Kachel.")


def _count(user):
    now = time.time()
    with _lock:
        lst = [t for t in _hits.get(user, []) if now - t < 60]
        if len(lst) >= PER_MIN:
            _hits[user] = lst
            raise TooMany()
        lst.append(now)
        _hits[user] = lst


def tile(z, x, y, user):
    """(Bytes, Content-Type) oder None, wenn Esri nichts Brauchbares liefert. ValueError bei ungueltiger Kachel."""
    _check(z, x, y)
    key = (z, x, y)
    with _lock:
        hit = _tiles.get(key)
    if hit:
        return hit
    _count(user or "-")
    try:
        r = httpx.get(URL.format(z=z, x=x, y=y), timeout=10, follow_redirects=False)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("Luftbild %s/%s/%s: %s", z, x, y, type(e).__name__)
        return None
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype not in ("image/jpeg", "image/png"):
        return None
    out = (r.content, ctype)
    with _lock:
        _tiles[key] = out
        while len(_tiles) > KEEP:
            _tiles.pop(next(iter(_tiles)))
    return out
