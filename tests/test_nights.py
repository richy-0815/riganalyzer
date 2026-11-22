"""Nachtplatz-Vorschlaege (v0.21.0): Tagesgrenzen, Fahrzeug-Filter, OSM-Abfrage, Einbindung in den Planer."""
import json
import sqlite3

import pytest
import shapely
from shapely import wkb

import nights
import osmdb
import planner

VEH = {"height": 2.9, "width": 2.33, "length": 7.43, "weight": 3.5}


def straight(km_total, minutes, n=1001, lat0=48.0, lon0=8.0):
    """Gerade Strecke nach Norden, km_total lang, gleichmaessig schnell -> ein Leg."""
    deg = km_total / 111.0
    shape = [(lat0 + deg * i / (n - 1), lon0) for i in range(n)]
    return {"shape": shape, "km": km_total, "sec": minutes * 60}


def test_short_stage_needs_no_night():
    w, _ = nights.windows([straight(300, 200)], 300, 500)
    assert w == []


def test_one_night_at_time_limit():
    # 1000 km in 10 h, Limit 5 h / 600 km: Zeit greift zuerst -> Grenze bei 5 h = 500 km, Rest passt
    w, _ = nights.windows([straight(1000, 600)], 300, 600)
    assert len(w) == 1
    assert w[0]["at_min"] == pytest.approx(300, abs=2) and w[0]["at_km"] == pytest.approx(500, abs=2)


def test_km_limit_wins_on_fast_roads():
    # Autobahn: 1200 km in 10 h, Limit 6 h / 500 km -> km greift zuerst
    w, _ = nights.windows([straight(1200, 600)], 360, 500)
    assert w[0]["at_km"] == pytest.approx(500, abs=2)
    assert len(w) == 2   # 500 + 500 + 200


def test_multi_leg_profile_is_continuous():
    a, b = straight(400, 240), straight(400, 240, lat0=48.0 + 400 / 111.0)
    lat, lon, km, mins = nights._profile([a, b])
    assert km[-1] == pytest.approx(800) and mins[-1] == pytest.approx(480)
    assert (abs(km[1:] - km[:-1]) < 5).all()   # kein Sprung am Leg-Grenzpunkt


class FakeDB:
    def __init__(self, els):
        self.els = els

    def covers(self, lat, lon):
        return True

    def camps_near(self, lat, lon, proj, radius):
        return self.els


def el(i, lat, tags, off=500):
    return ({"type": "node", "id": i, "lat": lat, "lon": 8.0, "tags": tags}, off)


def test_suggest_filters_vehicle_and_unsuitable_places():
    at = 48.0 + 500 / 111.0
    db = FakeDB([
        el(1, at, {"tourism": "caravan_site", "name": "Zu kurz", "maxlength": "6"}),
        el(2, at, {"tourism": "camp_site", "name": "Privat", "access": "private"}),
        el(3, at, {"tourism": "camp_site", "name": "Nur Zelte", "tents": "only"}),
        el(4, at, {"tourism": "caravan_site", "name": "Stellplatz Gut", "maxlength": "10", "maxweight": "7.5",
                   "sanitary_dump_station": "yes"}),
        el(5, at + 0.3, {"tourism": "camp_site", "name": "Camping Weiter"}, off=4000),
    ])
    items = nights.suggest([[straight(1000, 600)]], VEH, 300, 600, db)
    assert len(items) == 1
    names = [o["name"] for o in items[0]["options"]]
    assert names[0] == "Stellplatz Gut" and "Camping Weiter" in names
    assert not {"Zu kurz", "Privat", "Nur Zelte"} & set(names)
    assert items[0]["options"][0]["fits"] == "ok" and items[0]["options"][1]["fits"] == "unknown"


def test_suggest_without_db_reports_no_data():
    items = nights.suggest([[straight(1000, 600)]], VEH, 300, 600, None)
    assert items[0]["options"] == [] and items[0]["data"] is False


def _db(tmp_path, pois):
    path = str(tmp_path / "r.sqlite")
    con = sqlite3.connect(path)
    con.executescript("""
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE coverage(region TEXT PRIMARY KEY, wkb BLOB);
CREATE TABLE poi(id INTEGER PRIMARY KEY, osm TEXT UNIQUE, lat REAL, lon REAL, tags TEXT);
CREATE VIRTUAL TABLE poi_ix USING rtree(id, minlon, maxlon, minlat, maxlat);
""")
    con.execute("INSERT INTO meta VALUES ('schema','4'), ('regions','[\"europe/test\"]')")
    con.execute("INSERT INTO coverage VALUES ('europe/test', ?)", (wkb.dumps(shapely.box(7, 47, 9, 54)),))
    for i, (osm, la, lo, tags) in enumerate(pois, 1):
        con.execute("INSERT INTO poi VALUES (?,?,?,?,?)", (i, osm, la, lo, json.dumps(tags)))
        con.execute("INSERT INTO poi_ix VALUES (?,?,?,?,?)", (i, lo, lo, la, la))
    con.commit()
    return osmdb.OsmDBSet([osmdb.OsmDB(path)])


def test_osmdb_camps_near(tmp_path):
    db = _db(tmp_path, [("n1", 50.0, 8.02, {"tourism": "caravan_site", "name": "Nah"}),
                        ("w2", 50.0, 8.2, {"tourism": "camp_site", "name": "Zu weit"}),
                        ("n3", 50.0, 8.01, {"amenity": "fuel", "name": "Tanke"})])
    lat = [49.9, 50.0, 50.1]
    lon = [8.0, 8.0, 8.0]
    found = db.camps_near(lat, lon, nights._Proj(50.0, 8.0), 5000)
    assert [e["tags"]["name"] for e, _ in found] == ["Nah"]
    assert found[0][1] == pytest.approx(1430, abs=30)


def test_plan_returns_night_hints(monkeypatch):
    shape = straight(1000, 600)["shape"]

    class R:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            from tests_helpers import enc
            return {"trip": {"legs": [{"summary": {"length": 1000.0, "time": 36000}, "shape": enc(shape)}],
                             "summary": {"length": 1000.0, "time": 36000}}}

    monkeypatch.setattr(planner.httpx, "post", lambda *a, **k: R())
    at = 48.0 + 500 / 111.0
    monkeypatch.setattr(osmdb, "get", lambda: FakeDB([el(1, at, {"tourism": "caravan_site", "name": "Aire"})]))
    pts = [{"lat": shape[0][0], "lon": 8.0}, {"lat": shape[-1][0], "lon": 8.0}]
    r = planner.plan(pts, "rig", "fast", VEH, nights={"hours": 5, "km": 600})
    h = r["night_hints"]
    assert h["hours"] == 5 and len(h["items"]) == 1 and h["items"][0]["options"][0]["name"] == "Aire"
    assert planner.plan(pts, "rig", "fast", VEH)["night_hints"] is None
    assert planner.plan(pts, "ride", "fast", None, nights={"hours": 5})["night_hints"] is None
