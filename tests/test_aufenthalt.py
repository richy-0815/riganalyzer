"""Aufenthalte an Zwischenpunkten (0.47.0, Ticket #2): Planer, Uebernachtungs-Vorschlag, Speichern, Teilen, Reisepaket."""
import numpy as np
import pytest

import nights
import planner
import reisedaten
import shares
import trips
from test_planner_legs import A, B, D, Resp, _enc


@pytest.fixture
def vh(monkeypatch):
    """Valhalla-Attrappe wie test_planner_legs.valhalla: je Leg 10 km / 10 min pro Punktschritt."""
    calls = []

    def post(url, json=None, timeout=None):
        calls.append(json)
        locs = json["locations"]
        brk = [i for i, loc in enumerate(locs) if loc["type"] == "break"]
        legs = [{"summary": {"length": 10.0 * (b - a), "time": 600 * (b - a)},
                 "shape": _enc([(locs[k]["lat"], locs[k]["lon"]) for k in range(a, b + 1)])} for a, b in zip(brk, brk[1:])]
        return Resp({"trip": {"legs": legs, "summary": {"length": sum(x["summary"]["length"] for x in legs),
                                                          "time": sum(x["summary"]["time"] for x in legs)}}})
    monkeypatch.setattr(planner.httpx, "post", post)
    return calls


@pytest.mark.parametrize("v, want", [(90, 90), ("45", 45), (89.6, 90), (0, 0), (-5, 0), (2000, 1440), (None, 0), ("x", 0), (True, 0)])
def test_stay_min(v, want):
    assert planner.stay_min(v) == want


def test_stay_makes_break_and_leg_km(vh):
    r = planner.plan([A, {**B, "mode": "fast", "stay": 120}, D], "rig", "fast", {})
    assert [loc["type"] for loc in vh[0]["locations"]] == ["break", "break", "break"]
    assert r["legs"] == [{"km": 10.0, "min": 10, "to": 1}, {"km": 10.0, "min": 10, "to": 2}]
    assert len(r["stages"]) == 1   # Aufenthalt ist keine Uebernachtung


def test_without_stay_through_and_one_leg(vh):
    r = planner.plan([A, {**B, "mode": "fast"}, D], "rig", "fast", {})
    assert [loc["type"] for loc in vh[0]["locations"]] == ["break", "through", "break"]
    assert r["legs"] == [{"km": 20.0, "min": 20, "to": 2}]


def test_stay_ignored_at_start_end_and_night(vh):
    pts = [{**A, "stay": 60}, {**B, "mode": "fast", "stop": True, "stay": 60}, {**D, "stay": 60}]
    planner.plan(pts, "rig", "fast", {})
    assert not any("stay" in p for p in pts)


def test_nights_profile_counts_stay_as_time_not_distance():
    legs = [{"shape": [(48.0, 11.0), (48.0, 11.5)], "km": 40, "sec": 1800, "stay": 120},
            {"shape": [(48.0, 11.5), (48.0, 12.0)], "km": 40, "sec": 1800}]
    _, _, km, mins = nights._profile(legs)
    assert km[-1] == pytest.approx(80) and mins[-1] == pytest.approx(30 + 120 + 30)
    assert np.all(np.diff(mins) >= 0)


def test_trips_keep_stay_only_on_via():
    st = trips._stops([{"lat": 48, "lon": 11, "stay": 30}, {"lat": 48.1, "lon": 11.1, "stay": 90},
                       {"lat": 48.2, "lon": 11.2, "stop": True, "stay": 45}, {"lat": 48.3, "lon": 11.3, "stay": "x"}])
    assert [s.get("stay") for s in st] == [30, 90, None, None]


def test_shares_keep_stay_and_custom():
    out = shares._clean_plan({"pts": [{"lat": 48, "lon": 11, "name": "Dom", "custom": True, "stay": 90},
                                      {"lat": 48.1, "lon": 11.1, "custom": "ja", "stay": 5000}]})
    assert out["pts"][0]["custom"] is True and out["pts"][0]["stay"] == 90
    assert "custom" not in out["pts"][1] and "stay" not in out["pts"][1]


def test_reisedaten_via_has_stay():
    trip = {"name": "T", "vehicle": {}, "stops": [
        {"lat": 48, "lon": 11, "name": "A", "stop": False}, {"lat": 48.1, "lon": 11.1, "name": "B", "stop": False, "stay": 90},
        {"lat": 48.2, "lon": 11.2, "name": "C", "stop": False}]}
    out = reisedaten.build(trip, {"stages": [{"km": 20, "min": 30}]}, {"pois": [], "segments": []})
    assert out["stages"][0]["via"][0]["stay"] == 90
