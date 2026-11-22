"""Maut/Faehren meiden (0.42.0, Ticket #22): Planer, Navi-Route (RigNavi), Tour/Reisepaket."""
import pytest

import json as _json

import planner
from test_planner_legs import Resp as _Resp, _enc


class Resp(_Resp):
    text = ""

    @property
    def content(self):   # nav_route reicht die Antwort roh durch
        return _json.dumps(self._d).encode()

S = {"lat": 54.3, "lon": 10.1, "name": "Kiel"}
Z = {"lat": 55.6, "lon": 12.6, "name": "Kopenhagen"}


@pytest.fixture
def valhalla(monkeypatch):
    calls = []

    def post(url, json=None, timeout=None):
        calls.append(json)
        o = json["costing_options"][json["costing"]]
        locs = json["locations"]
        shape = _enc([(loc["lat"], loc["lon"]) for loc in locs])
        # Faehre laesst sich hier nicht vermeiden, Maut schon
        summ = {"length": 300.0, "time": 14400, "has_toll": o.get("use_tolls", 0.5) > 0, "has_ferry": True}
        return Resp({"trip": {"legs": [{"summary": summ, "shape": shape}], "summary": summ}})
    monkeypatch.setattr(planner.httpx, "post", post)
    return calls


def test_costing_nur_wenn_gewollt():
    _c, o = planner._rig_costing({}, False)
    assert "use_tolls" not in o and "use_ferry" not in o          # ohne Wunsch: Valhalla-Standard
    _c, o = planner._rig_costing({}, False, {"toll": True, "ferry": True})
    assert o["use_tolls"] == 0.0 and o["use_ferry"] == 0.0 and o["exclude_unpaved"] is True
    assert planner.avoid_flags({"toll": "ja", "ferry": 1}) == {"toll": False, "ferry": False}   # nur echtes true


def test_plan_meldet_was_sich_nicht_vermeiden_liess(valhalla):
    r = planner.plan([S, Z], "rig", "fast", {}, avoid={"toll": True, "ferry": True})
    assert valhalla[0]["costing_options"]["auto"]["use_tolls"] == 0.0
    assert r["has_toll"] is False and r["has_ferry"] is True
    assert r["avoid_note"] == "Fähre ließ sich nicht ganz vermeiden"
    r2 = planner.plan([S, Z], "rig", "fast", {})
    assert r2["has_ferry"] is True and r2["avoid_note"] is None   # ohne „meiden“ kein Hinweis (die Seite sagt „mit Fähre“)


def test_varianten_geben_meiden_weiter(valhalla):
    planner.variants([S, Z], {}, "fast", avoid={"ferry": True})
    assert all(c["costing_options"][c["costing"]].get("use_ferry") == 0.0 for c in valhalla)


def test_navi_route_parameter(monkeypatch, valhalla):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main.auth, "basic_user", lambda hdr, app: "olaf")
    c = TestClient(main.app, base_url="https://riganalyzer.petrick.top")
    body = {"locations": [{"lat": S["lat"], "lon": S["lon"]}, {"lat": Z["lat"], "lon": Z["lon"]}]}
    h = {"user-agent": "okhttp/5", "authorization": "Basic eDp5"}
    assert c.post("/api/nav/route?h=2.9&notoll=1&noferry=1", json=body, headers=h).status_code == 200
    o = valhalla[-1]["costing_options"][valhalla[-1]["costing"]]
    assert o["use_tolls"] == 0.0 and o["use_ferry"] == 0.0
    c.post("/api/nav/route?h=2.9", json=body, headers=h)
    assert "use_tolls" not in valhalla[-1]["costing_options"][valhalla[-1]["costing"]]


def test_tour_merkt_meiden(data_dir):
    import trips
    t = trips.save("olaf", {"name": "Fähre", "vehicle": {}, "stops": [S, Z], "avoid": {"ferry": True}})
    assert t["avoid"] == {"toll": False, "ferry": True}
    assert trips.save("olaf", {"name": "x", "vehicle": {}, "stops": [S, Z]})["avoid"] == {"toll": False, "ferry": False}
