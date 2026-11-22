import os

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import analyze
import osmdb
import reisedaten
from test_build_db_camp import _build


class FakeSet:
    def __init__(self, inside):
        self.inside = inside

    def in_camp(self, lat, lon):
        return self.inside


def test_camp_extra_kinds():
    assert analyze.camp_extra({"amenity": "water_point"}, 43.8, 6.5, FakeSet(True)) == {"water_kind": "guests"}
    assert analyze.camp_extra({"amenity": "water_point"}, 43.8, 6.5, FakeSet(False)) == {"water_kind": "public"}
    assert analyze.camp_extra({"amenity": "water_point"}, 43.8, 6.5, FakeSet(None)) == {"water_kind": "unknown"}
    assert analyze.camp_extra({"amenity": "drinking_water", "access": "private"}, 43.8, 6.5, FakeSet(False)) is None
    assert analyze.camp_extra({"tourism": "caravan_site"}, 43.8, 6.5, FakeSet(False)) == {"water_kind": None}


def test_dbset_in_camp_schema(tmp_path, monkeypatch):
    _build(tmp_path, monkeypatch)
    s = osmdb.OsmDBSet([osmdb.OsmDB(str(tmp_path / "test.sqlite"))])
    assert s.in_camp(43.8015, 6.5020) is True and s.in_camp(43.81, 6.52) is False
    assert s.in_camp(10.0, 10.0) is None          # keine Region deckt den Punkt ab


def test_near_stage_marks_places_around_stage_end():
    stages = [{"stats": {"km": 100.0}, "pois": [{"group": "camp", "km": 93.0}, {"group": "camp", "km": 50.0},
                                                   {"group": "fuel", "km": 99.0}]},
              {"stats": {"km": 40.0}, "pois": [{"group": "camp", "km": 4.0}]}]
    analyze.mark_near_stage(stages, km=10)
    assert stages[0]["pois"][0]["near_stage"] == 1 and "near_stage" not in stages[0]["pois"][1]
    assert "near_stage" not in stages[0]["pois"][2]
    assert stages[1]["pois"][0]["near_stage"] == 1     # kurz nach dem Etappenziel 1 (Start Etappe 2)


def test_reisedaten_passes_new_fields():
    a = {"trip": True, "stages": [{"stats": {"km": 10.0}, "rig": {}, "pois": [
        {"group": "camp", "label": "Frischwasser", "name": "", "lat": 1.0, "lon": 2.0, "km": 3.0,
         "water_kind": "public", "near_stage": 1, "flags": {"water": True}}]}]}
    trip = {"name": "x", "vehicle": {}, "stops": [{"lat": 0, "lon": 0, "name": "A"}, {"lat": 1, "lon": 1, "name": "B"}]}
    p = reisedaten.build(trip, {"stages": [{"km": 10, "min": 10}]}, a)["pois"][0]
    assert p["water_kind"] == "public" and p["near_stage"] == 1 and p["flags"]["water"] is True


def test_reisedaten_drinking_water_is_water_group():
    a = {"trip": True, "stages": [{"stats": {"km": 10.0}, "rig": {}, "pois": [
        {"group": "camp", "label": "Trinkwasser", "name": "", "lat": 1.0, "lon": 2.0, "km": 3.0,
         "water_kind": "tap", "flags": {"water": True}}]}]}
    trip = {"name": "x", "vehicle": {}, "stops": [{"lat": 0, "lon": 0, "name": "A"}, {"lat": 1, "lon": 1, "name": "B"}]}
    assert reisedaten.build(trip, {"stages": [{"km": 10, "min": 10}]}, a)["pois"][0]["group"] == "water"


def test_dump_station_access_like_water():
    dump = {"amenity": "sanitary_dump_station"}
    assert analyze.camp_extra(dump, 43.8, 6.5, FakeSet(True)) == {"water_kind": "guests"}
    assert analyze.camp_extra(dump, 43.8, 6.5, FakeSet(False)) == {"water_kind": "public"}
    assert analyze.camp_extra({**dump, "access": "customers"}, 43.8, 6.5, FakeSet(False)) == {"water_kind": "guests"}
    assert analyze.camp_extra({**dump, "access": "private"}, 43.8, 6.5, FakeSet(False)) is None


def test_reisedaten_dedupes_place_from_two_stages():
    p = {"group": "camp", "label": "Campingplatz", "name": "Les Lavandes", "lat": 43.8, "lon": 6.5, "flags": {}}
    a = {"trip": True, "stages": [{"stats": {"km": 10.0}, "rig": {}, "pois": [{**p, "km": 9.5}]},
                                  {"stats": {"km": 10.0}, "rig": {}, "pois": [{**p, "km": 0.4}]}]}
    trip = {"name": "x", "vehicle": {}, "stops": [{"lat": 0, "lon": 0, "name": "A"}, {"lat": 1, "lon": 1, "name": "B", "stop": True},
                                                 {"lat": 2, "lon": 2, "name": "C"}]}
    out = reisedaten.build(trip, {"stages": [{"km": 10, "min": 10}, {"km": 10, "min": 10}]}, a)["pois"]
    assert len(out) == 1 and out[0]["km"] == 9.5


def test_maxweight_in_kg():
    import rig
    assert rig.poi_flags({"tourism": "caravan_site", "maxweight": "3500 kg"}, "camp")["maxweight"] == 3.5
    assert rig.poi_flags({"tourism": "caravan_site", "maxweight": "7.5"}, "camp")["maxweight"] == 7.5
