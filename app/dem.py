"""Hoehen aus dem Copernicus-DEM (GLO-90, frei auf AWS Open Data).

1°x1°-Kacheln als Cloud-Optimized GeoTIFF (~5 MB), werden beim ersten Bedarf nach
DEM_DIR geladen und danach lokal gelesen – keine Limits wie bei Web-Diensten.
Kacheln ueber Meer gibt es nicht (404) -> Hoehe 0.
"""

import logging
import os
import threading
from collections import OrderedDict

import httpx
import numpy as np
import tifffile

log = logging.getLogger("dem")

DEM_DIR = os.environ.get("DEM_DIR", "/data/dem")
# QA: Prods Kacheln nur lesen; was dort fehlt, laedt QA in sein eigenes DEM_DIR (Prod-Daten unveraendert)
DEM_SHARED_DIR = os.environ.get("DEM_SHARED_DIR", "")
BASE = os.environ.get("DEM_URL", "https://copernicus-dem-90m.s3.amazonaws.com")
UA = "RideAnalyzer-Prototyp/0.5 (+https://riganalyzer.petrick.top)"
MAX_TILES_IN_RAM = 8

_cache = OrderedDict()
_lock = threading.Lock()


def _name(lat0, lon0):
    ns = f"N{lat0:02d}" if lat0 >= 0 else f"S{-lat0:02d}"
    ew = f"E{lon0:03d}" if lon0 >= 0 else f"W{-lon0:03d}"
    return f"Copernicus_DSM_COG_30_{ns}_00_{ew}_00_DEM"


def _tile(lat0, lon0):
    """-> (array, x0, y0, sx, sy) oder None (Meer/keine Kachel)."""
    key = (lat0, lon0)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    name = _name(lat0, lon0)
    path = os.path.join(DEM_DIR, name + ".tif")
    missing = path + ".none"
    if DEM_SHARED_DIR and not os.path.exists(path) and not os.path.exists(missing):
        shared = os.path.join(DEM_SHARED_DIR, name + ".tif")
        if os.path.exists(shared) or os.path.exists(shared + ".none"):
            path, missing = shared, shared + ".none"
    if os.path.exists(missing):
        tile = None
    else:
        if not os.path.exists(path):
            os.makedirs(DEM_DIR, exist_ok=True)
            r = httpx.get(f"{BASE}/{name}/{name}.tif", headers={"User-Agent": UA}, timeout=120)
            if r.status_code == 404:
                open(missing, "w").close()
                tile = None
                with _lock:
                    _cache[key] = tile
                return tile
            r.raise_for_status()
            with open(path + ".part", "wb") as f:
                f.write(r.content)
            os.replace(path + ".part", path)
            log.info("DEM-Kachel geladen: %s (%.1f MB)", name, len(r.content) / 1e6)
        with tifffile.TiffFile(path) as t:
            p = t.pages[0]
            a = p.asarray().astype(np.float32)
            sx, sy, _ = p.tags["ModelPixelScaleTag"].value
            tp = p.tags["ModelTiepointTag"].value
            tile = (a, float(tp[3]), float(tp[4]), float(sx), float(sy))
    with _lock:
        _cache[key] = tile
        while len(_cache) > MAX_TILES_IN_RAM:
            _cache.popitem(last=False)
    return tile


def sample(lat, lon):
    """Hoehen (m) fuer Punkt-Arrays, bilinear interpoliert."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    out = np.zeros(len(lat))
    keys = np.floor(lat).astype(int), np.floor(lon).astype(int)
    for la0, lo0 in set(zip(keys[0].tolist(), keys[1].tolist())):
        sel = (keys[0] == la0) & (keys[1] == lo0)
        t = _tile(la0, lo0)
        if t is None:
            continue
        a, x0, y0, sx, sy = t
        # Pixelmitten: Spalte/Zeile als Gleitkomma, dann bilinear
        c = (lon[sel] - x0) / sx - 0.5
        r = (y0 - lat[sel]) / sy - 0.5
        c0 = np.clip(np.floor(c).astype(int), 0, a.shape[1] - 2)
        r0 = np.clip(np.floor(r).astype(int), 0, a.shape[0] - 2)
        fc, fr = np.clip(c - c0, 0, 1), np.clip(r - r0, 0, 1)
        v = (a[r0, c0] * (1 - fc) * (1 - fr) + a[r0, c0 + 1] * fc * (1 - fr)
             + a[r0 + 1, c0] * (1 - fc) * fr + a[r0 + 1, c0 + 1] * fc * fr)
        out[sel] = v
    return out
