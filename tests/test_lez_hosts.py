"""Umweltzonen nur Rig (R7, seit v0.19.0): Ride-Host schickt nie exclude_polygons, Rig-Fallback meldet X-RA-LEZ-Note."""
from urllib.parse import unquote

import pytest
from shapely.geometry import MultiPolygon, box

import lez
import planner
from test_admin import basic, env  # noqa: F401  (Fixture env)
from test_planner_legs import A, D, Resp, _enc, valhalla  # noqa: F401  (Fixture)

RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"
BODY = {"locations": [{"lat": A["lat"], "lon": A["lon"]}, {"lat": D["lat"], "lon": D["lon"]}]}


@pytest.fixture
def zone(monkeypatch):
    z = [{"id": "fr:mid", "name": "ZFE Mitte", "country": "france", "source": "bnzfe", "rule": {"kind": "unknown"},
          "since": None, "until": None, "geom": MultiPolygon([box(6.2, 43.78, 6.3, 43.82)])}]
    monkeypatch.setattr(lez, "zones", lambda: z)


def _client(env, host):  # noqa: F811 – Fixture (Import oben)
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app, base_url=host)


def test_ride_plan_ignores_avoid_zones(env, zone, valhalla):  # noqa: F811 – Fixture (Import oben)
    c = _client(env, RIDE)
    r = c.post("/api/plan", json={"points": [A, D], "avoid_zones": ["fr:mid"]}, headers=basic("bofh"))
    assert r.status_code == 200, r.text
    assert valhalla and all("exclude_polygons" not in v for v in valhalla)


def test_rig_plan_does_send_them(env, zone, valhalla):    # Gegenprobe: der Test oben prueft wirklich etwas  # noqa: F811 – Fixture (Import oben)
    c = _client(env, RIG)
    r = c.post("/api/plan", json={"points": [A, D], "avoid_zones": ["fr:mid"]}, headers=basic("bofh"))
    assert r.status_code == 200, r.text
    assert "exclude_polygons" in valhalla[0]


def _post_recorder(monkeypatch, fail_with_exclusion):
    calls = []

    def post(url, json=None, timeout=None):
        calls.append(dict(json))
        if fail_with_exclusion and "exclude_polygons" in json:
            return Resp({"error_code": 442}, 400)
        return type("R", (), {"status_code": 200, "content": b'{"routes":[]}'})()
    monkeypatch.setattr(planner.httpx, "post", post)
    return calls


def test_ride_nav_route_ignores_lez(env, zone, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    calls = _post_recorder(monkeypatch, True)
    r = _client(env, RIDE).post("/api/nav/route?lez=fr:mid", json=BODY, headers=basic("bofh"))
    assert r.status_code == 200 and len(calls) == 1
    assert "exclude_polygons" not in calls[0] and "x-ra-lez-note" not in r.headers


def test_rig_nav_route_fallback_note_header(env, zone, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    calls = _post_recorder(monkeypatch, True)
    r = _client(env, RIG).post("/api/nav/route?lez=fr:mid", json=BODY, headers=basic("bofh"))
    assert r.status_code == 200 and len(calls) == 2
    assert "exclude_polygons" in calls[0] and "exclude_polygons" not in calls[1]
    assert "%" in r.headers["x-ra-lez-note"]                      # Umlaut -> %-kodiert
    assert unquote(r.headers["x-ra-lez-note"]) == "Umfahren nicht möglich"
