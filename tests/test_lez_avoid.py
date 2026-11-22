"""Umweltzonen umfahren (seit v0.19.0, nur Rig): avoid_zones -> Valhalla exclude_polygons."""
import pytest
from shapely.geometry import MultiPolygon, box

import lez
import planner
from test_planner_legs import A, D, Resp, _enc, valhalla  # noqa: F401  (Fixture)

# Zone um Start (A) bzw. Ziel (D) und eine in der Mitte
Z_MID = ("fr:mid", "ZFE Mitte", MultiPolygon([box(6.2, 43.78, 6.3, 43.82)]))
Z_END = ("fr:end", "ZFE Riez", MultiPolygon([box(6.05, 43.72, 6.12, 43.78)]))
Z_START = ("fr:start", "ZFE Castellane", MultiPolygon([box(6.48, 43.82, 6.55, 43.87)]))


def _zones(*zs):
    return [{"id": i, "name": n, "country": "france", "source": "bnzfe", "rule": {"kind": "unknown"},
             "since": None, "until": None, "geom": g} for i, n, g in zs]


@pytest.fixture
def zs(monkeypatch):
    z = _zones(Z_MID, Z_END, Z_START)
    monkeypatch.setattr(lez, "zones", lambda: z)
    return z


def test_plan_sends_exclude_polygons(valhalla, zs):  # noqa: F811 – Fixture (Import oben)
    r = planner.plan([A, D], "rig", "fast", {}, avoid_zones=["fr:mid", "unbekannt"])
    polys = valhalla[0]["exclude_polygons"]
    assert len(polys) == 1 and len(polys[0]) >= 4 and polys[0][0] == polys[0][-1]
    lon, lat = polys[0][0]
    assert 6.2 <= lon <= 6.3 and 43.78 <= lat <= 43.82      # [lon, lat]
    assert r["lez_note"] is None


def test_no_avoid_no_key(valhalla, zs):  # noqa: F811 – Fixture (Import oben)
    r = planner.plan([A, D], "rig", "fast", {})
    assert "exclude_polygons" not in valhalla[0] and r["lez_note"] is None


def test_start_or_end_zone_not_excluded(valhalla, zs):  # noqa: F811 – Fixture (Import oben)
    r = planner.plan([A, D], "rig", "fast", {}, avoid_zones=["fr:mid", "fr:end"])
    assert len(valhalla[0]["exclude_polygons"]) == 1
    assert r["lez_note"] == "Ziel liegt in der Umweltzone ZFE Riez"
    r = planner.plan([A, D], "rig", "fast", {}, avoid_zones=["fr:start"])
    assert "exclude_polygons" not in valhalla[-1]
    assert r["lez_note"] == "Start liegt in der Umweltzone ZFE Castellane"


def test_no_route_with_exclusion_falls_back(monkeypatch, zs):
    calls = []

    def post(url, json=None, timeout=None):
        calls.append(dict(json))
        if "exclude_polygons" in json:
            return Resp({"error_code": 442, "error": "No path could be found"}, 400)
        return Resp({"trip": {"legs": [{"summary": {"length": 5.0, "time": 300},
                                         "shape": _enc([(A["lat"], A["lon"]), (D["lat"], D["lon"])])}],
                              "summary": {"length": 5.0, "time": 300}}})
    monkeypatch.setattr(planner.httpx, "post", post)
    r = planner.plan([A, D], "rig", "fast", {}, avoid_zones=["fr:mid"])
    assert len(calls) == 2 and "exclude_polygons" not in calls[1]
    assert r["km"] == 5.0 and r["lez_note"] == "Umfahren nicht möglich"


def test_max_20(monkeypatch):
    z = _zones(*[(f"z{i}", f"Z{i}", MultiPolygon([box(7 + i * .01, 45, 7.005 + i * .01, 45.005)])) for i in range(25)])
    monkeypatch.setattr(lez, "zones", lambda: z)
    polys, kept = lez.exclude_polygons([f"z{i}" for i in range(25)], [])
    assert len(polys) == 20 and kept == []


def test_multipolygon_one_ring_per_part_counts_once(monkeypatch):
    g = MultiPolygon([box(7, 45, 7.1, 45.1), box(8, 45, 8.1, 45.1)])
    monkeypatch.setattr(lez, "zones", lambda: _zones(("m", "M", g)))
    polys, kept = lez.exclude_polygons(["m"], [])
    assert len(polys) == 2 and kept == []


def test_keep_points_returns_ids(monkeypatch, zs):
    polys, kept = lez.exclude_polygons(["fr:mid", "fr:end"], [(43.7486, 6.0911)])
    assert kept == ["fr:end"] and len(polys) == 1


def test_no_data_means_no_exclusion(monkeypatch):
    monkeypatch.setattr(lez, "zones", lambda: None)
    assert lez.exclude_polygons(["x"], []) == ([], [])


def test_ride_ignores_avoid_zones(valhalla, zs):  # noqa: F811 – Fixture (Import oben)
    r = planner.plan([A, D], "ride", "fast", None, avoid_zones=["fr:mid"])
    assert "exclude_polygons" not in valhalla[0] and r["lez_note"] is None


def test_nav_route_excludes_and_falls_back(monkeypatch, zs):
    calls = []

    def post(url, json=None, timeout=None):
        calls.append(dict(json))
        if "exclude_polygons" in json:
            return Resp({"error_code": 442}, 400)
        return type("R", (), {"status_code": 200, "content": b'{"routes":[]}'})()
    monkeypatch.setattr(planner.httpx, "post", post)
    body = {"locations": [{"lat": A["lat"], "lon": A["lon"]}, {"lat": D["lat"], "lon": D["lon"]}]}
    st, content, costing, note = planner.nav_route(body, {}, False, avoid_zones=["fr:mid"])
    assert "exclude_polygons" in calls[0] and "exclude_polygons" not in calls[1]
    assert (st, content, note) == (200, b'{"routes":[]}', "Umfahren nicht möglich")
    calls.clear()
    st, content, costing, note = planner.nav_route(body, {}, False, avoid_zones=["fr:end"])
    assert "exclude_polygons" not in calls[0] and note == "Ziel liegt in der Umweltzone ZFE Riez"


@pytest.mark.parametrize("bad", ["fr:mid", {"fr:mid": 1}, 5, True])
def test_avoid_zones_not_a_list_ignored(valhalla, zs, bad):  # noqa: F811 – Fixture (Import oben)
    r = planner.plan([A, D], "rig", "fast", {}, avoid_zones=bad)
    assert "exclude_polygons" not in valhalla[0] and r["lez_note"] is None


def _failing_post(calls):
    def post(url, json=None, timeout=None):
        calls.append(dict(json))
        if "exclude_polygons" in json:
            return Resp({"error_code": 442, "error": "No path could be found"}, 400)
        return Resp({"trip": {"legs": [{"summary": {"length": 5.0, "time": 300},
                                         "shape": _enc([(A["lat"], A["lon"]), (D["lat"], D["lon"])])}],
                              "summary": {"length": 5.0, "time": 300}}})
    return post


def test_fallback_joins_notes_plan(monkeypatch, zs):
    monkeypatch.setattr(planner.httpx, "post", _failing_post([]))
    r = planner.plan([A, D], "rig", "fast", {}, avoid_zones=["fr:mid", "fr:end"])
    assert r["lez_note"] == "Ziel liegt in der Umweltzone ZFE Riez; Umfahren nicht möglich"


def test_fallback_joins_notes_nav_route(monkeypatch, zs):
    def post(url, json=None, timeout=None):
        if "exclude_polygons" in json:
            return Resp({"error_code": 442}, 400)
        return type("R", (), {"status_code": 200, "content": b"{}"})()
    monkeypatch.setattr(planner.httpx, "post", post)
    body = {"locations": [{"lat": A["lat"], "lon": A["lon"]}, {"lat": D["lat"], "lon": D["lon"]}]}
    note = planner.nav_route(body, {}, False, avoid_zones=["fr:mid", "fr:end"])[3]
    assert note == "Ziel liegt in der Umweltzone ZFE Riez; Umfahren nicht möglich"
    assert planner.nav_route(body, {}, False, avoid_zones="fr:mid")[3] is None
