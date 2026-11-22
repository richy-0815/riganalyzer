"""Umweltzonen an der Rig-Route (seit v0.19.0): Schnitt der Route mit den Zonen, nur in der Rig-Analyse."""
import json
import os
import shutil

import numpy as np
import pytest
from shapely.geometry import MultiPolygon, box

import analyze
import build_db
import lez
import main
import osmdb

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _zone(zid, geom, name="Testzone"):
    return {"id": zid, "name": name, "country": "france", "source": "bnzfe", "rule": {"kind": "unknown"},
            "since": None, "until": None, "geom": MultiPolygon([geom])}


def _line(km_total=30.0):
    """Gerade Ost-West-Route auf 50 N, ein Punkt je ~1 km."""
    n = int(km_total) + 1
    lat = np.full(n, 50.0)
    lon = 8.0 + np.arange(n) * (1.0 / (111.32 * np.cos(np.radians(50.0))))
    return lat, lon, np.arange(n, dtype=float)


def _lon_at(km):
    return 8.0 + km / (111.32 * np.cos(np.radians(50.0)))


def test_route_through_zone_km(monkeypatch):
    lat, lon, km = _line()
    monkeypatch.setattr(lez, "zones", lambda: [_zone("a", box(_lon_at(10), 49.99, _lon_at(14), 50.01))])
    out = lez.on_route(lat, lon, km)
    assert len(out) == 1
    z = out[0]
    assert abs(z["km0"] - 10) < 0.1 and abs(z["km1"] - 14) < 0.1
    assert set(z) == {"id", "name", "country", "source", "km0", "km1", "rule", "since", "until", "poly"}
    ring = z["poly"][0]
    assert all(49.98 < p[0] < 50.02 and 8.0 < p[1] < 8.5 for p in ring)   # [lat, lon]


def test_touch_under_200m_ignored(monkeypatch):
    lat, lon, km = _line()
    zs = [_zone("kurz", box(_lon_at(10), 49.99, _lon_at(10.15), 50.01)),
          _zone("lang", box(_lon_at(20), 49.99, _lon_at(20.3), 50.01)),
          _zone("abseits", box(_lon_at(5), 50.2, _lon_at(9), 50.3))]
    monkeypatch.setattr(lez, "zones", lambda: zs)
    assert [z["id"] for z in lez.on_route(lat, lon, km)] == ["lang"]


def test_sorted_by_km0_and_stages_kept(monkeypatch):
    lat, lon, km = _line()
    zs = [_zone("b", box(_lon_at(20), 49.99, _lon_at(22), 50.01), "B"),
          _zone("a1", box(_lon_at(5), 49.99, _lon_at(8), 50.01), "A"),
          _zone("a2", box(_lon_at(5), 49.99, _lon_at(8), 50.01), "A")]   # R6: Stufen unveraendert durchreichen
    monkeypatch.setattr(lez, "zones", lambda: zs)
    assert [z["id"] for z in lez.on_route(lat, lon, km)] == ["a1", "a2", "b"]


def test_no_zone_data_none(monkeypatch):
    lat, lon, km = _line()
    monkeypatch.setattr(lez, "zones", lambda: None)
    assert lez.on_route(lat, lon, km) is None


# ---------------------------------------------------------------- Analyse (Fixture-Strasse aus gap.osm)

@pytest.fixture
def gapdb(tmp_path, monkeypatch):
    def fake_download(region, workdir):
        os.makedirs(workdir, exist_ok=True)
        shutil.copy(os.path.join(FIX, "gap.osm"), os.path.join(workdir, "gap.osm"))
        return os.path.join(workdir, "gap.osm"), os.path.join(FIX, "gap.poly")
    monkeypatch.setattr(build_db, "download", fake_download)
    dst = str(tmp_path / "gap.sqlite")
    build_db.build(dst, ["europe/test"], str(tmp_path / "work"))
    s = osmdb.OsmDBSet([osmdb.OsmDB(dst)])
    monkeypatch.setattr(osmdb, "get", lambda: s)
    return s


def _gpx():
    """Dichte Spur entlang der Fixture-Strasse: 2 km nach Norden, dann ~4 km nach Osten."""
    pts = [(43.8 + i * 0.0009, 6.45, None) for i in range(21)]
    pts += [(43.818, 6.45 + i * 0.001, None) for i in range(1, 61)]
    return analyze.stage_gpx(pts, "lez")


def _keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from _keys(v)


def test_ride_has_no_lez(gapdb, monkeypatch):
    zone = _zone("fr:1", box(6.47, 43.80, 6.49, 43.83))
    monkeypatch.setattr(lez, "zones", lambda: [zone])
    ride = analyze.analyze(_gpx(), "lez.gpx", options={"elevation": False})
    assert "lez" not in set(_keys(json.loads(json.dumps(ride, default=str))))
    rg = analyze.analyze(_gpx(), "lez.gpx", options={"rig": True, "elevation": False})
    zs = rg["rig"]["lez"]
    assert isinstance(zs, list) and [z["id"] for z in zs] == ["fr:1"]
    assert zs[0]["km1"] > zs[0]["km0"] > 2


def test_rig_lez_none_without_data(gapdb, monkeypatch):
    monkeypatch.setattr(lez, "zones", lambda: None)
    rg = analyze.analyze(_gpx(), "lez.gpx", options={"rig": True, "elevation": False})
    assert "lez" in rg["rig"] and rg["rig"]["lez"] is None


def test_trip_stages_each_have_lez(gapdb, monkeypatch):
    zone = _zone("fr:1", box(6.47, 43.80, 6.49, 43.83))
    monkeypatch.setattr(lez, "zones", lambda: [zone])
    pts = [(43.8 + i * 0.0009, 6.45, None) for i in range(21)] + [(43.818, 6.45 + i * 0.001, None) for i in range(1, 61)]
    stages = [("A", 1, pts), ("B", 1, pts)]
    res = main._run_trip(stages, "Reise", "t.gpx", lambda p, m: None, {}, {"rig": True, "elevation": False})
    assert len(res["stages"]) == 2
    assert all(isinstance(s["rig"]["lez"], list) for s in res["stages"])


def test_demo_key_changes_with_zone_stand(tmp_path, monkeypatch):
    monkeypatch.setattr(lez, "DIR", str(tmp_path))
    name = next(f[:-4] for f in os.listdir(os.path.join(main.HERE, "static", "demo")) if f.endswith(".gpx"))
    (tmp_path / "meta.json").write_text(json.dumps({"bnzfe": "2026-10-01T00:00:00+00:00", "osm": "x"}))
    k1 = main._demo_key(name, True)
    (tmp_path / "meta.json").write_text(json.dumps({"bnzfe": "2026-10-02T00:00:00+00:00", "osm": "x"}))
    assert main._demo_key(name, True) != k1


def test_demo_key_changes_with_shops_dataset(monkeypatch):
    import shops

    class DB:
        def __init__(self, built):
            self.meta = {"built": built}

    name = next(f[:-4] for f in os.listdir(os.path.join(main.HERE, "static", "demo")) if f.endswith(".gpx"))
    monkeypatch.setattr(shops, "get", lambda: None)
    k_none = main._demo_key(name, True)
    monkeypatch.setattr(shops, "get", lambda: DB("2026-11-01"))
    k1 = main._demo_key(name, True)
    monkeypatch.setattr(shops, "get", lambda: DB("2026-12-01"))
    assert len({k_none, k1, main._demo_key(name, True)}) == 3


def test_engine_hash_covers_shops_py():
    """Seit 0.46.6 meldet die Rig-Erweiterung ihre Dateien fuer den Fingerabdruck an (hooks "engine.files")."""
    import hooks
    assert {"rig.py", "shops.py"} <= {f for fn in hooks.all_("engine.files") for f in fn()}
    src = open(os.path.join(main.HERE, "main.py"), encoding="utf-8").read()
    assert 'hooks.all_("engine.files")' in src.split("def _engine_hash")[1].split("_ENGINE =")[0]
