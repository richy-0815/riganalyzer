import json
import os

import pytest

import reisedaten

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "analysis_small.json")
TRIP = {"id": "abc123abc123", "name": "Verdon", "vehicle": {"height": 3.0, "trailer": True},
        "stops": [{"lat": 52.52, "lon": 13.40, "name": "Berlin", "stop": False},
                  {"lat": 50.58, "lon": 8.47, "name": "Dutenhofener See", "stop": True},
                  {"lat": 47.35, "lon": 6.36, "name": "Baume-les-Dames", "stop": False}]}
PLAN = {"km": 997.9, "minutes": 600, "stages": [{"km": 525.3, "min": 270}, {"km": 472.6, "min": 330}]}


@pytest.fixture
def out():
    return reisedaten.build(TRIP, PLAN, json.load(open(FIX)))


def test_stages_from_stops(out):
    st = out["stages"]
    assert [s["n"] for s in st] == [1, 2]
    assert (st[0]["from"], st[0]["to"]) == ("Berlin", "Dutenhofener See")
    assert (st[1]["from"], st[1]["to"]) == ("Dutenhofener See", "Baume-les-Dames")
    assert (st[1]["lat"], st[1]["lon"]) == (47.35, 6.36) and st[0]["km"] == 525.3 and st[1]["minutes"] == 330


def test_warnings_all_types_with_trip_km(out):
    w = out["warnings"]
    assert len(w) == 2 * (3 + 2 + 1)
    assert {x["type"] for x in w} == {"restriction", "steep", "hairpin"}
    assert all({"lat", "lon", "km0", "km1", "label"} <= set(x) for x in w)
    second = [x for x in w if x["km0"] > 525.3]
    assert len(second) == 6                     # Etappe 2 um 525,3 km verschoben
    assert w == sorted(w, key=lambda x: x["km0"])


def test_pois_groups_and_motorway(out):
    groups = {p["group"] for p in out["pois"]}
    # auch die Unterwegs-Orte; Rastplaetze/Picknick weiter nicht
    assert {"fuel", "dump", "water", "camp"} <= groups <= {"fuel", "dump", "water", "camp", "sight", "food", "service"}
    assert "sight" in groups
    assert any(p["motorway"] for p in out["pois"] if p["group"] == "fuel")
    assert all(p["motorway"] is False for p in out["pois"] if p["group"] != "fuel")
    assert max(p["km"] for p in out["pois"]) > 525.3


def test_single_stage_analysis_shape():
    a = json.load(open(FIX))["stages"][0]          # ohne "trip": Ergebnis einer Einzelroute
    trip = {**TRIP, "stops": TRIP["stops"][:2]}
    o = reisedaten.build(trip, {"stages": [PLAN["stages"][0]]}, a)
    assert len(o["stages"]) == 1 and len(o["warnings"]) == 6


def test_vehicle_and_countries(out):
    assert out["vehicle"]["height"] == 3.0
    assert out["countries"] and out["countries"][0]["cc"] == "DE"


def test_stage_via_points():
    trip = {**TRIP, "stops": [TRIP["stops"][0], {"lat": 51.0, "lon": 11.0, "name": "Erfurt", "stop": False},
                              TRIP["stops"][1], TRIP["stops"][2]]}
    o = reisedaten.build(trip, PLAN, json.load(open(FIX)))
    assert [v["name"] for v in o["stages"][0]["via"]] == ["Erfurt"]
    assert o["stages"][1]["via"] == []


def test_stage_and_via_carry_leg_mode():
    trip = {**TRIP, "stops": [TRIP["stops"][0], {"lat": 51.0, "lon": 11.0, "name": "Erfurt", "stop": False, "mode": "noway"},
                              {**TRIP["stops"][1], "mode": "fast"}, TRIP["stops"][2]]}
    o = reisedaten.build(trip, PLAN, json.load(open(FIX)))
    assert o["stages"][0]["via"][0]["mode"] == "noway" and o["stages"][0]["mode"] == "fast"
    assert o["stages"][1]["mode"] is None


def _zone(i, km0=1.0):
    return {"id": i, "name": "Z" + i, "country": "FR", "source": "bnzfe", "km0": km0, "km1": km0 + 2,
            "rule": {"vp": 2}, "since": None, "until": None, "poly": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}


def test_package_has_lez():
    a = json.load(open(FIX))
    for st in a["stages"]:
        st.setdefault("rig", {})["lez"] = [_zone("a")]
    a["stages"][0]["rig"]["lez"].append(_zone("b"))
    o = reisedaten.build(TRIP, PLAN, a)
    assert [z["id"] for z in o["lez"]] == ["a", "b"]
    assert set(o["lez"][0]) == {"id", "name", "country", "rule", "since", "until", "poly"}


def test_package_lez_null_without_zone_data():
    a = json.load(open(FIX))
    for st in a["stages"]:
        st.setdefault("rig", {})["lez"] = None
    assert reisedaten.build(TRIP, PLAN, a)["lez"] is None


def test_tourism_office_infos_in_package(monkeypatch):
    """0.24.0: Plaetze bekommen die Angaben der franz. Tourismusaemter, fehlende kommen als „only“ dazu."""
    import frcamps
    import reisedaten
    seen = {}

    def fake(cps, line):
        seen["cps"], seen["line"] = cps, line
        return {"match": {cps[0][0]: {"n": "Les Roches", "k": "camping", "s": 2, "d": "161 emplacements", "w": "https://x.fr",
                                      "id": "u1", "a": "", "u": "2026-10-23", "r": "", "p": ""}},
                "extra": [{"n": "Aire publique", "k": "aire", "lat": 43.64, "lon": 4.82, "km": 7.5, "off": 300, "id": "u2",
                           "s": None, "d": "", "w": "", "a": "", "u": "", "r": "", "p": ""}]}
    monkeypatch.setattr(frcamps, "lookup", fake)
    pois = [{"group": "camp", "label": "Campingplatz", "name": "Les Roches", "lat": 43.76, "lon": 6.15, "km": 3.0,
             "flags": {}, "water_kind": None, "near_stage": None},
            {"group": "fuel", "label": "Tankstelle", "name": "", "lat": 43.7, "lon": 6.1, "km": 9.0, "flags": {}}]
    reisedaten._add_fr(pois, [(43.76, 6.15, 3.0)])
    assert seen["cps"] == [("0", 43.76, 6.15, "Les Roches")]
    assert pois[0]["fr"]["s"] == 2 and "id" not in pois[0]["fr"]
    extra = [p for p in pois if p.get("fr", {}).get("only")]
    assert len(extra) == 1 and extra[0]["label"] == "Wohnmobil-Stellplatz" and [p["km"] for p in pois] == [3.0, 7.5, 9.0]


def test_shop_pois_in_package():
    a = json.load(open(FIX))
    shop = {"km": 10.0, "lat": 51.0, "lon": 9.0, "off": 0.4, "group": "shop", "label": "Supermarkt", "name": "Edeka",
            "flags": {"kind": "supermarket", "brand": "Edeka", "hours": "Mo-Sa 07:00-20:00", "addr": "Hauptstr. 1"},
            "osm": "node/42"}
    a["stages"][1]["pois"].append(dict(shop))
    a["stages"][1]["pois"].append(dict(shop))          # Dublette (Nacht-Punkt-Streifen)
    pk = reisedaten.build(TRIP, PLAN, a)
    got = [p for p in pk["pois"] if p["group"] == "shop"]
    assert len(got) == 1
    p = got[0]
    assert p["label"] == "Supermarkt" and p["name"] == "Edeka" and p["osm"] == "node/42"
    assert (p["lat"], p["lon"]) == (51.0, 9.0) and p["motorway"] is False
    assert p["flags"] == shop["flags"]
    assert p["km"] == 535.3            # Etappen-Offset wie bei fuel/camp
