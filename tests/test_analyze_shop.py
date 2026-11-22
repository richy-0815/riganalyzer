"""Einkaufen unterwegs (#7): Laeden als POI-Gruppe shop in der Rig-Analyse, Hinweise, letzter Laden je Etappe."""
import os
import shutil

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import analyze
import build_db
import main
import osmdb
import shops

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


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


PTS = [(43.8 + i * 0.0009, 6.45, None) for i in range(21)] + [(43.818, 6.45 + i * 0.001, None) for i in range(1, 61)]


def _gpx():
    return analyze.stage_gpx(PTS, "shop")


class FakeDB:
    def __init__(self):
        self.calls = 0

    def near(self, line, max_m=2000):
        self.calls += 1
        return [
            {"osm": "n1", "lat": 43.81, "lon": 6.45, "kind": "discount", "name": "Lidl", "brand": "Lidl",
             "hours": "Mo-Sa 07:00-20:00", "addr": "Hauptstr. 1", "off": 120, "km": 1.0},
            {"osm": "w2", "lat": 43.818, "lon": 6.46, "kind": "bakery", "name": "", "brand": "Kamps",
             "hours": None, "addr": None, "off": 40, "km": 3.0},
        ]


def _de(monkeypatch, db):
    monkeypatch.setattr(shops, "get", lambda: db)
    monkeypatch.setattr(shops, "touches_germany", lambda line: True)


def test_rig_has_shop_pois(gapdb, monkeypatch):
    db = FakeDB()
    _de(monkeypatch, db)
    r = analyze.analyze(_gpx(), "s.gpx", options={"rig": True, "elevation": False})
    sh = {p["osm"]: p for p in r["pois"] if p["group"] == "shop"}
    assert set(sh) == {"node/1", "way/2"} and db.calls == 1
    a = sh["node/1"]
    assert a["label"] == "Discounter" and a["name"] == "Lidl" and a["km"] == 1.0 and a["off"] == 120
    assert a["flags"] == {"kind": "discount", "brand": "Lidl", "hours": "Mo-Sa 07:00-20:00", "addr": "Hauptstr. 1"}
    assert sh["way/2"]["label"] == "Bäcker" and sh["way/2"]["name"] == "Kamps"
    assert not [n for n in r["notes"] if n.get("t") == "shop"]


def test_ride_unaffected(gapdb, monkeypatch):
    db = FakeDB()
    _de(monkeypatch, db)
    r = analyze.analyze(_gpx(), "s.gpx", options={"elevation": False})
    assert db.calls == 0 and not [p for p in r["pois"] if p["group"] == "shop"]
    assert not [n for n in r["notes"] if n.get("t") == "shop"]


def test_missing_data_hint(gapdb, monkeypatch):
    monkeypatch.setattr(shops, "get", lambda: None)
    monkeypatch.setattr(shops, "touches_germany", lambda line: True)
    r = analyze.analyze(_gpx(), "s.gpx", options={"rig": True, "elevation": False})
    assert [n["label"] for n in r["notes"] if n.get("t") == "shop"] == [shops.MISSING_HINT]
    assert not [p for p in r["pois"] if p["group"] == "shop"]


def test_outside_germany_hint(gapdb, monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(shops, "get", lambda: db)   # Fixture liegt in Frankreich
    r = analyze.analyze(_gpx(), "s.gpx", options={"rig": True, "elevation": False})
    assert [n["label"] for n in r["notes"] if n.get("t") == "shop"] == [shops.DE_HINT]
    assert db.calls == 0 and not [p for p in r["pois"] if p["group"] == "shop"]


def test_mark_last_shop():
    stages = [{"stats": {"km": 100.0}, "pois": [
        {"group": "shop", "km": 40.0, "name": "A", "label": "Supermarkt", "flags": {"kind": "supermarket", "hours": None}},
        {"group": "shop", "km": 97.0, "name": "Lidl", "label": "Discounter", "flags": {"kind": "discount", "hours": "Mo-Sa 07:00-20:00"}},
        {"group": "shop", "km": 101.0, "name": "zu spät", "label": "Bäcker", "flags": {"kind": "bakery", "hours": None}}]},
        {"stats": {"km": 30.0}, "pois": [{"group": "camp", "km": 5.0}]}]
    analyze.mark_last_shop(stages)
    assert stages[0]["last_shop"] == {"name": "Lidl", "label": "Discounter", "kind": "discount", "km_before": 3.0,
                                      "hours": "Mo-Sa 07:00-20:00"}
    assert "last_shop" not in stages[1]


def test_trip_stage_gets_last_shop(gapdb, monkeypatch):
    _de(monkeypatch, FakeDB())
    res = main._run_trip([("A", 1, PTS)], "Reise", "t.gpx", lambda p, m: None, {}, {"rig": True, "elevation": False})
    st = res["stages"][0]
    assert st["last_shop"]["name"] == "Kamps" and st["last_shop"]["kind"] == "bakery"
    assert st["last_shop"]["km_before"] == round(st["stats"]["km"] - 3.0, 1)


def test_near_error_gives_missing_hint(gapdb, monkeypatch):
    class Broken:
        def near(self, line, max_m=2000):
            raise OSError("disk I/O error")
    _de(monkeypatch, Broken())
    r = analyze.analyze(_gpx(), "s.gpx", options={"rig": True, "elevation": False})
    hints = [n for n in r["notes"] if n.get("t") == "shop"]
    assert [n["label"] for n in hints] == [shops.MISSING_HINT] and hints[0]["text"]
    assert not [p for p in r["pois"] if p["group"] == "shop"]


def test_osm_ref():
    assert analyze._osm_ref("n123") == "node/123"
    assert analyze._osm_ref("w9") == "way/9"
    assert analyze._osm_ref("r5") == "relation/5"
    assert analyze._osm_ref("node/1") == "node/1" and analyze._osm_ref(None) == ""
