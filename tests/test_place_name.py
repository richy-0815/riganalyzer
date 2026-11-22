"""Namensvorschlag fuer per Klick gesetzte Planer-Punkte (0.47.0, Ticket #9) – lokal aus place=* der Regionen-DB."""
import os
import shutil

import pytest

import build_db
import osmdb

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.mark.parametrize("cands, want", [
    ([("Wetzlar", "town", 1500), ("Gießen", "city", 9000)], "Wetzlar"),          # im Ort
    ([("Gießen", "city", 9000), ("Dorf", "village", 2900)], "bei Gießen"),       # Stadt relativ naeher als das Dorf
    ([("Dorf", "village", 700)], "Dorf"),
    ([("Dorf", "village", 1200)], "bei Dorf"),
    ([("Weiler", "hamlet", 2000)], ""),                                          # zu weit fuer einen Weiler
    ([("", "town", 100), ("Ort", "unbekannt", 10)], ""),                          # ohne Namen / fremde Art
    ([], ""),
])
def test_pick_place(cands, want):
    assert osmdb.pick_place(cands) == want


@pytest.fixture
def placedb(tmp_path, monkeypatch):
    def fake_download(region, workdir):
        os.makedirs(workdir, exist_ok=True)
        pbf = os.path.join(workdir, "places.osm")
        shutil.copy(os.path.join(FIX, "places.osm"), pbf)
        return pbf, os.path.join(FIX, "camp.poly")
    monkeypatch.setattr(build_db, "download", fake_download)
    dst = str(tmp_path / "places.sqlite")
    build_db.build(dst, ["europe/test"], str(tmp_path / "work"))
    return osmdb.OsmDBSet([osmdb.OsmDB(dst)])


def test_place_name_from_db(placedb):
    assert placedb.place_name(43.8010, 6.5010) == "Castellane"          # ~140 m vom Ortspunkt
    assert placedb.place_name(43.7650, 6.4450) == "La Palud"            # ~700 m vom Dorf
    assert placedb.place_name(43.7550, 6.4250) == "bei La Palud"        # ~1,3 km vom Dorf, Castellane ~7 km
    # gleich weit relativ zur Reichweite: die groessere Stadt gewinnt (1,9 km Dorf vs. 4,6 km Kleinstadt)
    assert placedb.place_name(43.7700, 6.4600) == "bei Castellane"
    assert placedb.place_name(43.8450, 6.5550) == "bei Castellane"      # namenloser Weiler (700 m) zaehlt nicht
    assert placedb.place_name(43.8800, 6.5800) == ""                    # 10,9 km: zu weit fuer eine Kleinstadt (8 km)


def test_places_near_ignores_unnamed_and_non_places(placedb):
    names = {n for n, _, _ in placedb.dbs[0].places_near(43.85, 6.56, 15000)}
    assert names == {"Castellane", "La Palud"}


def test_outside_coverage_is_empty(placedb):
    assert placedb.place_name(48.0, 11.0) == ""


def test_api_place(placedb, monkeypatch, data_dir):
    import json

    from fastapi.testclient import TestClient

    import auth
    import main
    (data_dir / "users.json").write_text(json.dumps({"users": {"olaf": {"hash": auth.hash_pw("geheim12345"), "apps": ["ride", "rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    host = "https://riganalyzer.petrick.top"
    c = TestClient(main.app, base_url=host)
    assert c.get("/api/place", params={"lat": 43.801, "lon": 6.501}).status_code == 401   # nur angemeldet
    c.post("/login", data={"user": "olaf", "password": "geheim12345"},
           headers={"origin": host, "user-agent": "Mozilla/5.0"}, follow_redirects=False)
    monkeypatch.setattr(osmdb, "get", lambda: placedb)
    assert c.get("/api/place", params={"lat": 43.801, "lon": 6.501}).json() == {"name": "Castellane"}
    assert c.get("/api/place", params={"lat": 95, "lon": 6.5}).status_code == 422
    monkeypatch.setattr(osmdb, "get", lambda: None)
    assert c.get("/api/place", params={"lat": 43.801, "lon": 6.501}).json() == {"name": ""}
