"""Alternativrouten (0.41.0, Ticket #21): Valhalla-Alternativen bei Start -> Ziel, Gegenvariante zur Autobahn."""
import pytest

import planner
from test_planner_legs import Resp, _enc

S = {"lat": 48.0, "lon": 10.0, "name": "Start"}
Z = {"lat": 48.0, "lon": 11.0, "name": "Ziel"}


@pytest.fixture
def valhalla(monkeypatch):
    calls = []

    def post(url, json=None, timeout=None):
        calls.append(json)
        locs = json["locations"]
        noway = json["costing_options"][json["costing"]]["use_highways"] == 0.0
        a, b = (locs[0]["lat"], locs[0]["lon"]), (locs[-1]["lat"], locs[-1]["lon"])
        main = [a, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), b]
        km = 90.0 if noway else 75.0
        res = {"trip": {"legs": [{"summary": {"length": km, "time": 7200 if noway else 3600}, "shape": _enc(main)}],
                        "summary": {"length": km, "time": 7200 if noway else 3600}}}
        if json.get("alternates"):
            # Alternative 1: weit nach Norden (Abweichung ~22 km), Alternative 2: praktisch gleich wie die Hauptroute
            north = [a, (48.2, 10.5), b]
            res["alternates"] = [
                {"trip": {"legs": [{"summary": {"length": 82.0, "time": 4200}, "shape": _enc(north)}],
                          "summary": {"length": 82.0, "time": 4200}}},
                {"trip": {"legs": [{"summary": {"length": 75.2, "time": 3610}, "shape": _enc(main)}],
                          "summary": {"length": 75.2, "time": 3610}}},
            ]
        return Resp(res)
    monkeypatch.setattr(planner.httpx, "post", post)
    return calls


def test_start_ziel_alternative_mit_zwischenpunkt_und_ohne_autobahn(valhalla):
    v = planner.variants([S, Z], {}, "fast")
    assert [x["id"] for x in v] == ["aktuell", "alt1", "noway"]          # alt2 = fast gleich -> weg
    assert v[0]["apply"] is None and v[0]["km"] == 75.0
    via = v[1]["apply"]["via"]
    assert abs(via["lat"] - 48.2) < 0.01 and abs(via["lon"] - 10.5) < 0.01   # Stelle groesster Abweichung
    assert 0 <= via["heading"] < 360
    assert v[2]["apply"] == {"mode": "noway"} and v[2]["label"] == "Ohne Autobahn" and v[2]["minutes"] == 120
    assert all(len(x["coords"]) >= 2 for x in v)
    assert any(c.get("alternates") == 2 for c in valhalla)


def test_mit_zwischenpunkten_keine_valhalla_alternativen(valhalla):
    mid = {"lat": 48.1, "lon": 10.5, "name": "Mitte"}
    v = planner.variants([S, mid, Z], {}, "fast")
    assert [x["id"] for x in v] == ["aktuell", "noway"]
    assert not any(c.get("alternates") for c in valhalla)


def test_alles_ohne_autobahn_bietet_mit_autobahn(valhalla):
    v = planner.variants([S, {**Z, "mode": "noway"}], {}, "noway")
    assert v[-1]["id"] == "fast" and v[-1]["label"] == "Mit Autobahn" and v[-1]["apply"] == {"mode": "fast"}


def test_api_nur_rig(monkeypatch, valhalla):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main.auth, "basic_user", lambda hdr, app: "olaf")
    ride = TestClient(main.app, base_url="https://rideanalyzer.petrick.top")
    h = {"user-agent": "okhttp/5", "authorization": "Basic eDp5"}
    assert ride.post("/api/plan/variants", json={"points": [S, Z]}, headers=h).status_code == 404
    rig = TestClient(main.app, base_url="https://riganalyzer.petrick.top")
    r = rig.post("/api/plan/variants", json={"points": [S, Z], "vehicle": {}}, headers={"user-agent": "okhttp/5", "authorization": "Basic eDp5"})
    assert r.status_code == 200 and r.json()["variants"][0]["id"] == "aktuell"
