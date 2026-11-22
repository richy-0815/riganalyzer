import base64
import json
import os

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import auth
import package

STOPS = [{"lat": 45.70, "lon": 4.85, "name": "Lyon"}, {"lat": 43.847, "lon": 6.513, "name": "Castellane", "stop": False}]
RIG = {"host": "riganalyzer.petrick.top", "user-agent": "okhttp/5.5.1"}


def basic(user, pw="geheim"):
    return {**RIG, "authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


@pytest.fixture
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient

    h = auth.hash_pw("geheim")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "richy": {"hash": h, "apps": ["ride", "rig"]}, "olaf": {"hash": h, "apps": ["rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    monkeypatch.setattr(package, "request", lambda user, tid: None)
    import main
    return TestClient(main.app)


def _fake_package(data_dir, user, tid, content=b"0123456789" * 10):
    d = data_dir / "packages" / user / tid / "20261025120000000000"
    d.mkdir(parents=True)
    (d / "strassen.tar").write_bytes(content)
    m = {"format": 1, "trip_id": tid, "name": "x", "built": "2026-10-25T12:00:00", "vehicle": {}, "polygon": [],
         "data_dates": {}, "files": [{"name": "strassen.tar", "size": len(content), "sha256": "-"}]}
    (d / "paket.json").write_text(json.dumps(m))
    (d.parent / "secret.txt").write_text("nein")


def test_create_and_list_only_own(client):
    r = client.post("/api/trips", headers=basic("richy"), json={"name": "Verdon", "stops": STOPS})
    assert r.status_code == 200
    tid = r.json()["trip"]["id"]
    lst = client.get("/api/trips", headers=basic("richy")).json()
    assert [t["id"] for t in lst] == [tid] and lst[0]["stages"] == 1 and lst[0]["package"]["state"] in ("none", "queued")
    assert client.get("/api/trips", headers=basic("olaf")).json() == []
    assert client.get(f"/api/trips/{tid}", headers=basic("olaf")).status_code == 404
    assert client.get(f"/api/trips/{tid}", headers=basic("richy")).json()["trip"]["name"] == "Verdon"


def test_invalid_trip_is_422(client):
    assert client.post("/api/trips", headers=basic("richy"), json={"stops": STOPS[:1]}).status_code == 422


def test_ride_host_has_no_trips(client):
    h = {**basic("richy"), "host": "rideanalyzer.petrick.top"}
    assert client.get("/api/trips", headers=h).status_code == 404


def test_package_manifest_and_range(client, data_dir):
    tid = client.post("/api/trips", headers=basic("richy"), json={"stops": STOPS}).json()["trip"]["id"]
    assert client.get(f"/api/trips/{tid}/package", headers=basic("richy")).status_code == 404
    _fake_package(data_dir, "richy", tid)
    assert client.get(f"/api/trips/{tid}/package", headers=basic("richy")).json()["trip_id"] == tid
    r = client.get(f"/api/trips/{tid}/package/strassen.tar", headers={**basic("richy"), "range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == b"0123456789"
    assert client.get(f"/api/trips/{tid}/package/strassen.tar", headers=basic("olaf")).status_code == 404


def test_package_paths_are_confined(client, data_dir):
    tid = client.post("/api/trips", headers=basic("richy"), json={"stops": STOPS}).json()["trip"]["id"]
    _fake_package(data_dir, "richy", tid)
    for bad in ("../secret.txt", "..%2Fsecret.txt", "paket.json", "nicht-da.bin"):
        assert client.get(f"/api/trips/{tid}/package/{bad}", headers=basic("richy")).status_code == 404


def test_delete(client, data_dir):
    tid = client.post("/api/trips", headers=basic("richy"), json={"stops": STOPS}).json()["trip"]["id"]
    _fake_package(data_dir, "richy", tid)
    assert client.delete(f"/api/trips/{tid}", headers=basic("olaf")).status_code == 404
    assert client.delete(f"/api/trips/{tid}", headers=basic("richy")).status_code == 204
    assert client.get("/api/trips", headers=basic("richy")).json() == []
    assert not (data_dir / "packages" / "richy" / tid).exists()


def test_delete_package_only(client, data_dir):
    """0.33.0: nur das Paket vom Server loeschen – die Reise bleibt, fremde/Ride-Host 404, laufender Bau 409."""
    tid = client.post("/api/trips", headers=basic("richy"), json={"stops": STOPS}).json()["trip"]["id"]
    _fake_package(data_dir, "richy", tid)
    assert client.delete(f"/api/trips/{tid}/package", headers=basic("olaf")).status_code == 404
    assert client.delete(f"/api/trips/{tid}/package",
                         headers={**basic("richy"), "host": "rideanalyzer.petrick.top"}).status_code == 404
    assert client.delete(f"/api/trips/{tid}/package", headers=basic("richy")).status_code == 204
    assert not (data_dir / "packages" / "richy" / tid).exists()
    lst = client.get("/api/trips", headers=basic("richy")).json()
    assert [t["id"] for t in lst] == [tid] and lst[0]["package"]["state"] == "none"
    assert client.delete(f"/api/trips/{tid}/package", headers=basic("richy")).status_code == 204   # nochmal: still ok


def test_delete_package_refused_while_building(client, data_dir):
    tid = client.post("/api/trips", headers=basic("richy"), json={"stops": STOPS}).json()["trip"]["id"]
    _fake_package(data_dir, "richy", tid)
    package._set_status("richy", tid, state="building", msg="Karte", progress=40)
    r = client.delete(f"/api/trips/{tid}/package", headers=basic("richy"))
    assert r.status_code == 409 and "gebaut" in r.json()["detail"]
    assert (data_dir / "packages" / "richy" / tid).exists()


def test_rebuild_package(client, data_dir, monkeypatch):
    """0.51.0 (#21): „Neu bauen“ – nur eigene Reise, nicht waehrend eines Baus, Warteschlangen-Grenze als 429."""
    calls = []
    monkeypatch.setattr(package, "request", lambda user, tid: calls.append((user, tid)))
    tid = client.post("/api/trips", headers=basic("richy"), json={"name": "x", "stops": STOPS}).json()["trip"]["id"]
    calls.clear()
    r = client.post(f"/api/trips/{tid}/package", headers=basic("richy"))
    assert r.status_code == 200 and calls == [("richy", tid)] and "steps" in r.json()["package"]
    assert client.post(f"/api/trips/{tid}/package", headers=basic("olaf")).status_code == 404
    package._set_status("richy", tid, state="building", msg="x", progress=40)
    assert client.post(f"/api/trips/{tid}/package", headers=basic("richy")).status_code == 409
    package._set_status("richy", tid, state="error", msg="x", progress=0)

    def too_many(user, tid):
        raise package.TooMany("Schon 2 Navi-Pakete in Arbeit")
    monkeypatch.setattr(package, "request", too_many)
    r = client.post(f"/api/trips/{tid}/package", headers=basic("richy"))
    assert r.status_code == 429 and "Schon 2" in r.json()["detail"]
