"""Fotos aus Wikimedia Commons fuer die Detail-Karten (seit 0.54.0, Ticket #31).

OSM-Orte verweisen mit `wikimedia_commons=File:…` auf ein freies Foto. Der Browser laedt es nicht selbst bei Wikimedia
(Datenschutz, wie Luftbild und Mapillary seit 0.52.1): unser Server holt eine Vorschau (480 px breit) ueber
Special:FilePath, nimmt nur Bilder von Wikimedia-Hosts (upload/thumb) und merkt sich die letzten im RAM. Bremse je Login.
"""
import logging
import re
import threading
import time
from urllib.parse import quote, urlsplit

import httpx

log = logging.getLogger("fotos")

UA = "RigAnalyzer/1 (Detail-Karten; +https://riganalyzer.petrick.top)"
URL = "https://commons.wikimedia.org/wiki/Special:FilePath/{}?width=480"
HOSTS = ("upload.wikimedia.org", "thumb.wikimedia.org", "commons.wikimedia.org")
NAME = re.compile(r"^[^/\\#?<>\[\]|{}\x00-\x1f]{1,200}\.(?:jpe?g|png|webp|gif|tiff?)$", re.I)
KEEP = 120                 # Vorschauen im Speicher (je ~30-80 KB)
MAX_BYTES = 3 * 1024 * 1024
PER_MIN = 60               # je Login und Minute
_cache = {}
_hits = {}
_lock = threading.Lock()


class TooMany(Exception):
    pass


def valid(name):
    return bool(NAME.match(name or "")) and ".." not in name


def _count(user):
    now = time.time()
    with _lock:
        lst = [t for t in _hits.get(user, []) if now - t < 60]
        if len(lst) >= PER_MIN:
            _hits[user] = lst
            raise TooMany()
        lst.append(now)
        _hits[user] = lst


def commons(name, user):
    """(Bytes, Content-Type) der Vorschau oder None. ValueError bei ungueltigem Dateinamen, TooMany bei zu vielen Abrufen."""
    name = (name or "").strip().replace(" ", "_")
    if not valid(name):
        raise ValueError("Ungültiger Dateiname.")
    with _lock:
        hit = _cache.get(name)
    if hit:
        return hit
    _count(user or "-")
    url = URL.format(quote(name))
    try:
        with httpx.Client(timeout=15, headers={"User-Agent": UA}, follow_redirects=False) as c:
            for _ in range(4):
                r = c.get(url)
                if r.is_redirect:
                    url = str(r.next_request.url) if r.next_request else ""
                    if urlsplit(url).hostname not in HOSTS:
                        return None
                    continue
                break
            else:
                return None
    except httpx.HTTPError as e:
        log.info("Commons %s: %s", name, type(e).__name__)
        return None
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if r.status_code != 200 or not ctype.startswith("image/") or ctype == "image/svg+xml" or len(r.content) > MAX_BYTES:
        return None
    out = (r.content, ctype)
    with _lock:
        _cache[name] = out
        while len(_cache) > KEEP:
            _cache.pop(next(iter(_cache)))
    return out
