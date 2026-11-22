import json
import os
import shutil
import sqlite3

import build_db

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _build(tmp_path, monkeypatch):
    def fake_download(region, workdir):
        os.makedirs(workdir, exist_ok=True)
        pbf = os.path.join(workdir, "camp.osm")
        shutil.copy(os.path.join(FIX, "camp.osm"), pbf)
        return pbf, os.path.join(FIX, "camp.poly")
    monkeypatch.setattr(build_db, "download", fake_download)
    dst = str(tmp_path / "test.sqlite")
    build_db.build(dst, ["europe/test"], str(tmp_path / "work"))
    return sqlite3.connect(dst)


def test_schema4_camp_area_and_tags(tmp_path, monkeypatch):
    db = _build(tmp_path, monkeypatch)
    assert db.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == "4"
    assert db.execute("SELECT COUNT(*) FROM land WHERE cls='tourism=camp'").fetchone()[0] == 1
    tags = [json.loads(t) for (t,) in db.execute("SELECT tags FROM poi")]
    dw = [t for t in tags if t.get("amenity") == "drinking_water"]
    assert dw and dw[0]["access"] == "yes"
    aire = next(t for t in tags if t.get("name") == "Aire Test")
    assert aire["maxlength"] == "10" and aire["fee"] == "no" and aire["dog"] == "yes" and aire["shower"] == "no"


def test_camp_class_does_not_break_landscape_scoring():
    import analyze
    kv = "tourism=camp"
    assert analyze.LAND_CLASS.get(kv.split("=", 1)[1]) is None   # wird uebersprungen, nicht als Landschaft gewertet
