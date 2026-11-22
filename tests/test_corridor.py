import io
import tarfile

import shapely
from shapely.geometry import Point

import corridor

ROUTE = [(45.70 + i * (43.847 - 45.70) / 50, 4.85 + i * (6.513 - 4.85) / 50) for i in range(51)]  # Lyon -> Castellane
CASTELLANE, KASSEL = "2/000/771/146.gph", "2/000/814/357.gph"


def test_tile_bbox_castellane():
    assert corridor.tile_bbox(CASTELLANE) == (43.75, 6.5, 44.0, 6.75)
    assert corridor.tile_bbox("1/003/456.gph") is not None
    assert corridor.tile_bbox("index.bin") is None


def test_select_keeps_near_drops_far():
    assert corridor.select([CASTELLANE, KASSEL, "index.bin"], [(43.847, 6.513)], 25) == [CASTELLANE]


def test_select_keeps_level0_tile_containing_route():
    # Level 0, 4°: Zeile 33 (42..46 N), Spalte 46 (4..8 E) -> 33*90+46 = 3016
    assert corridor.select(["0/003/016.gph"], [(43.847, 6.513)], 25) == ["0/003/016.gph"]


def _tar(path, names):
    with tarfile.open(path, "w") as t:
        for n in names:
            data = n.encode()
            info = tarfile.TarInfo(n)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


def test_extract_has_no_index_bin(tmp_path):
    src, dst = tmp_path / "all.tar", tmp_path / "part.tar"
    _tar(src, ["index.bin", CASTELLANE, KASSEL])
    info = corridor.write_extract(str(src), str(dst), 25, [(43.847, 6.513)])
    with tarfile.open(dst) as t:
        assert t.getnames() == [CASTELLANE]
        assert t.extractfile(CASTELLANE).read() == CASTELLANE.encode()
    assert info == {"tiles": 1, "bytes": len(CASTELLANE)}


def test_outline_contains_route_and_excludes_far():
    ring = corridor.outline(ROUTE, 20)
    poly = shapely.Polygon(ring)
    assert poly.contains(Point(6.513, 43.847)) and poly.contains(Point(4.85, 45.70))
    assert poly.contains(Point(6.513 + 0.2, 43.847))          # ~16 km oestlich: drin
    assert not poly.contains(Point(6.513 + 0.4, 43.847))      # ~32 km: draussen
    assert not poly.contains(Point(9.48, 51.31))
    assert len(ring) <= 600 and ring[0] == ring[-1]


def test_geojson_is_feature_collection():
    g = corridor.geojson([[0, 0], [1, 0], [1, 1], [0, 0]])
    assert g["type"] == "FeatureCollection" and g["features"][0]["geometry"]["type"] == "Polygon"


def test_loop_route_keeps_hole():
    # Rundkurs ~100 x 100 km: Mitte darf NICHT im 20-km-Streifen liegen
    sq = [(45.0, 5.0), (45.0, 6.3), (45.9, 6.3), (45.9, 5.0), (45.0, 5.0)]
    pts = []
    for (a, b), (c, d) in zip(sq, sq[1:]):
        pts += [(a + (c - a) * i / 50, b + (d - b) * i / 50) for i in range(50)]
    rings = corridor.outline_rings(pts, 20)
    poly = shapely.Polygon(rings[0], rings[1:])
    assert len(rings) >= 2
    assert poly.contains(Point(5.0, 45.45)) and not poly.contains(Point(5.65, 45.45))
    g = corridor.geojson(rings[0], rings[1:])
    assert len(g["features"][0]["geometry"]["coordinates"]) == len(rings)
