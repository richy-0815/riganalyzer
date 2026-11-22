"""Streifen um eine Reise fuer das RigNavi-Offline-Paket.

- Valhalla-Kacheln (Level 0/1/2), die naeher als buf_km an der Route liegen, in ein eigenes tar
  (OHNE index.bin: dessen Offsets zeigen ins grosse Archiv -> Valhalla fande keine Strassen)
- Umriss als Polygon: daran prueft die App, ob sie selbst rechnen kann
"""

import math
import tarfile

import numpy as np
import shapely
from shapely.geometry import LineString

TILE_DEG = {0: 4.0, 1: 1.0, 2: 0.25}


def tile_bbox(name):
    """'2/000/771/146.gph' -> (lat0, lon0, lat1, lon1); None fuer alles andere."""
    parts = name.strip("./").split("/")
    if len(parts) < 2 or not parts[0].isdigit() or int(parts[0]) not in TILE_DEG or not name.endswith(".gph"):
        return None
    size = TILE_DEG[int(parts[0])]
    idx = int("".join(parts[1:])[:-4])
    cols = int(360 / size)
    lat0, lon0 = -90 + (idx // cols) * size, -180 + (idx % cols) * size
    return (lat0, lon0, lat0 + size, lon0 + size)


def _thin(pts, km=1.0):
    out, last = [], None
    for la, lo in pts:
        if last is None or math.hypot((la - last[0]) * 111, (lo - last[1]) * 111 * math.cos(math.radians(la))) >= km:
            out.append((la, lo))
            last = (la, lo)
    if pts and out[-1] != tuple(pts[-1]):
        out.append(tuple(pts[-1]))
    return np.array(out, float)


def _near(bbox, arr, buf_km):
    lat0, lon0, lat1, lon1 = bbox
    dla = buf_km / 111.0
    dlo = buf_km / (111.0 * np.cos(np.radians(arr[:, 0])))
    return bool(np.any((arr[:, 0] >= lat0 - dla) & (arr[:, 0] <= lat1 + dla)
                       & (arr[:, 1] >= lon0 - dlo) & (arr[:, 1] <= lon1 + dlo)))


def select(names, pts, buf_km):
    arr = _thin(pts)
    return [n for n in names if (b := tile_bbox(n)) and _near(b, arr, buf_km)]


def write_extract(src_tar, dst_tar, buf_km, pts):
    arr = _thin(pts)
    n = size = 0
    with tarfile.open(src_tar) as src, tarfile.open(dst_tar, "w") as dst:
        for m in src:
            b = tile_bbox(m.name) if m.isfile() else None
            if b and _near(b, arr, buf_km):
                dst.addfile(m, src.extractfile(m))
                n += 1
                size += m.size
    return {"tiles": n, "bytes": size}


def outline(pts, km, max_vertices=600):
    """Aussenring (GeoJSON [lon, lat]) im Abstand km um die Route (ohne Loecher, s. outline_rings)."""
    return outline_rings(pts, km, max_vertices)[0]


def outline_rings(pts, km, max_vertices=600):
    """[Aussenring, Loch, ...] im Abstand km um die Route – Rundreisen haben ein Loch in der Mitte,
    das NICHT zum Reisegebiet gehoert (dort gibt es keine Kacheln)."""
    arr = _thin(pts, 0.5)
    lat_m = float(arr[:, 0].mean())
    kx = 111_320 * math.cos(math.radians(lat_m))    # gleichabstaendig um die Mittelbreite
    line = LineString([(lo * kx, la * 110_540) for la, lo in arr]) if len(arr) > 1 else None
    geom = line.buffer(km * 1000) if line else shapely.Point(arr[0][1] * kx, arr[0][0] * 110_540).buffer(km * 1000)
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    tol, g = 200.0, geom
    while len(g.exterior.coords) + sum(len(r.coords) for r in g.interiors) > max_vertices:
        g = geom.simplify(tol)
        tol *= 2
    def conv(ring):
        return [[round(x / kx, 5), round(y / 110_540, 5)] for x, y in ring.coords]

    return [conv(g.exterior)] + [conv(r) for r in g.interiors]


def geojson(ring, holes=()):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring, *holes]}}]}
