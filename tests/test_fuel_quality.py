"""Datenqualitaet + Tankstellen-Abstecher hinter Autobahn-Ausfahrten (seit v0.11)."""
import os
import shutil

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import analyze
import build_db
import osmdb
import regions
import rig

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def mwdb(tmp_path, monkeypatch):
    def fake_download(region, workdir):
        os.makedirs(workdir, exist_ok=True)
        pbf = os.path.join(workdir, "motorway.osm")
        shutil.copy(os.path.join(FIX, "motorway.osm"), pbf)
        return pbf, os.path.join(FIX, "camp.poly")
    monkeypatch.setattr(build_db, "download", fake_download)
    dst = str(tmp_path / "mw.sqlite")
    build_db.build(dst, ["europe/test"], str(tmp_path / "work"))
    s = osmdb.OsmDBSet([osmdb.OsmDB(dst)])
    monkeypatch.setattr(osmdb, "get", lambda: s)
    monkeypatch.setattr(regions, "missing_for", lambda *a, **k: [])
    return s


def _gpx():
    pts = "".join(f'<trkpt lat="43.8000" lon="{6.455 + k * 0.0012:.4f}"/>' for k in range(100))
    return (f'<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            f"<trk><name>A99</name><trkseg>{pts}</trkseg></trk></gpx>").encode()


def test_fuel_class_and_card_only():
    assert rig.fuel_class({"brand": "JET"}, False) == "discount"
    assert rig.fuel_class({"brand": "Aral"}, False) == "marke"
    assert rig.fuel_class({"brand": "Aral"}, True) == "rast"
    assert rig.fuel_class({"name": "Autohof Lohfelden", "brand": "Shell"}, False) == "autohof"
    assert rig.fuel_class({"name": "Tankstelle Meier"}, False) == "frei"
    assert rig.fuel_class({"brand": "E.Leclerc"}, False) == "discount"
    assert rig.poi_flags({"brand": "AS 24"}, "fuel")["card_only"] is True
    assert rig.poi_flags({"brand": "Aral"}, "fuel")["card_only"] is False
    assert rig.poi_flags({"name": "Flüssiggas J. Ritter", "fuel:lpg": "yes"}, "fuel")["diesel"] is False
    assert rig.poi_flags({"name": "Autogas Nord", "fuel:diesel": "yes"}, "fuel")["diesel"] is True
    assert rig.poi_flags({"name": "Tankstelle Meier"}, "fuel")["diesel"] is True


def test_rig_finds_detour_station_behind_exit(mwdb):
    r = analyze.analyze(_gpx(), "a99.gpx", options={"rig": True, "elevation": False})
    fuel = {p["name"]: p for p in r["pois"] if p["group"] == "fuel"}
    assert fuel["Aral Rastanlage"]["flags"]["cls"] == "rast"
    jet = fuel["JET"]
    assert jet["flags"]["cls"] == "discount" and jet["off"] > 2000
    # 1,5 km hinter dem Ende der Ausfahrt: 2 x 1,5 x 1,3 + 1 km Rampen
    assert 4.5 <= jet["flags"]["detour_km"] <= 5.3
    assert abs(jet["km"] - 5.1) < 0.6           # an der Ausfahrt eingeordnet
    assert "AS24" not in fuel                     # nur mit Lkw-Tankkarte: kein Abstecher wert
    assert "Shell" not in fuel                    # 5 km hinter der Ausfahrt: zu weit
    assert r["stats"]["fuel_km"] == [fuel["Aral Rastanlage"]["km"]]   # Abstecher zaehlen nicht als „an der Strecke“


def test_ride_has_no_detour_stations(mwdb):
    r = analyze.analyze(_gpx(), "a99.gpx", options={"elevation": False})
    assert not [p for p in r["pois"] if p["group"] == "fuel" and p["flags"].get("detour_km")]


def test_quality_block(mwdb):
    q = analyze.analyze(_gpx(), "a99.gpx", options={"rig": True, "elevation": False})["quality"]
    assert q["matched"] >= 95 and q["surface"] >= 95 and q["speed"] >= 95 and q["width"] >= 95
    assert q["elevation"] is False and q["source"] == "lokal" and q["osm_date"]
    assert q["fuel"]["n"] == 1 and q["fuel"]["diesel"] == 0     # nur die Raststaette liegt an der Strecke
    assert q["score"] == 80 and q["grade"] == "gut"             # alles bekannt, nur das Hoehenmodell fehlt
    ride = analyze.analyze(_gpx(), "a99.gpx", options={"elevation": False})["quality"]
    assert "fuel" not in ride and "width" not in ride and ride["score"] >= 95


def test_reisedaten_skips_detour_and_card_only():
    import reisedaten
    def mk(name, **f):
        return {"group": "fuel", "label": "Tankstelle", "name": name, "lat": 1.0, "lon": 2.0, "km": 3.0,
                "flags": {"diesel": True, **f}}

    a = {"trip": True, "stages": [{"stats": {"km": 10.0}, "rig": {}, "pois": [
        mk("an der Strecke"), mk("Abstecher", detour_km=4.2), mk("AS24", card_only=True)]}]}
    trip = {"name": "x", "vehicle": {}, "stops": [{"lat": 0, "lon": 0, "name": "A"}, {"lat": 1, "lon": 1, "name": "B"}]}
    assert [p["name"] for p in reisedaten.build(trip, {"stages": [{"km": 10, "min": 10}]}, a)["pois"]] == ["an der Strecke"]
