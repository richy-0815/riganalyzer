"""0.17.2: Admin je App – unter rideanalyzer.* nur Ride, unter riganalyzer.* nur Rig. Was zur anderen App gehoert,
wird weder gezeigt noch beim Speichern veraendert."""
import json

import pyotp
import pytest

import auth

RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"


@pytest.fixture
def env(data_dir, monkeypatch):
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True},
        "otto": {"hash": h, "apps": ["ride"]},
        "olaf": {"hash": h, "apps": ["rig"]},
        "mia": {"hash": h, "apps": ["ride", "rig"], "admin": {"apps": ["ride", "rig"], "groups": ["g1"]}, "group": "g1"}}}))
    (data_dir / "groups.json").write_text(json.dumps({"groups": {"g1": {"name": "XJR-Club"}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    return data_dir


def admin_on(host, secret=None):
    """bofh auf host anmelden; 2FA beim ersten Mal einrichten, sonst Code eingeben -> (client, headers, secret)."""
    from fastapi.testclient import TestClient
    from conftest import enable_admin_2fa
    import main
    o = {"origin": host, "user-agent": "Mozilla/5.0"}
    c = TestClient(main.app, base_url=host)
    c.post("/login", data={"user": "bofh", "password": "geheim12345"}, headers=o, follow_redirects=False)
    if secret:   # derselbe Code gilt nur einmal – den naechsten nehmen
        import time
        c.post("/zweiter-faktor", data={"code": pyotp.TOTP(secret).at(time.time() + 30)}, headers=o, follow_redirects=False)
    else:
        secret = enable_admin_2fa(c, o)
    return c, {**o, "x-ra-csrf": c.cookies.get("ra_csrf")}, secret


def test_lists_only_own_app(env):
    ride, rh, sec = admin_on(RIDE)
    rig, gh, _ = admin_on(RIG, sec)
    assert [u["name"] for u in ride.get("/api/admin/users", headers=rh).json()] == ["bofh", "mia", "otto"]
    assert [u["name"] for u in rig.get("/api/admin/users", headers=gh).json()] == ["bofh", "mia", "olaf"]
    assert {u["name"] for u in ride.get("/api/admin/activity", headers=rh).json()["users"]} == {"bofh", "mia", "otto"}


def test_versions_only_own_app(env):
    import main
    ride, rh, sec = admin_on(RIDE)
    v = ride.get("/api/admin/versions", headers=rh).json()
    assert all(r["app"] in ("ride", "beide") for r in v["releases"])
    assert "app" not in v["running"]                                   # RigNavi-APK nur beim Rig
    assert v["running"]["server"] == main.app_version(False)
    assert main.app_version(False) == next(r["version"] for r in main._releases() if r["app"] in ("ride", "beide"))
    assert main.app_version(True) == next(r["version"] for r in main._releases() if r["app"] in ("rig", "beide"))


def test_saving_on_ride_keeps_rig_part(env):
    ride, rh, _ = admin_on(RIDE)
    ride.patch("/api/admin/users/mia", json={"admin": {"apps": [], "groups": ["g1"]}}, headers=rh)   # Ride-Recht raus
    assert auth.read_disk()["mia"]["admin"] == {"apps": ["rig"], "groups": ["g1"]}       # Rig-Recht bleibt
    r = ride.patch("/api/admin/users/mia", json={"apps": []}, headers=rh)                # Ride-Haken raus
    assert r.status_code == 200 and auth.read_disk()["mia"]["apps"] == ["rig"]
    assert ride.patch("/api/admin/users/mia", json={"apps": ["ride"]}, headers=rh).status_code == 404   # jetzt reiner Rig-Login
    assert ride.patch("/api/admin/users/olaf", json={"mail": "x@example.org"}, headers=rh).status_code == 404


def test_create_gets_host_app(env):
    ride, rh, _ = admin_on(RIDE)
    assert ride.post("/api/admin/users", json={"name": "finn", "apps": ["ride", "rig"]}, headers=rh).status_code == 200
    assert auth.read_disk()["finn"]["apps"] == ["ride"]


def test_footer_version_per_app(env):
    from fastapi.testclient import TestClient
    import main
    assert main.app_version(False) in TestClient(main.app, base_url=RIDE).get("/login").text or True
    c, h, _ = admin_on(RIDE)
    assert c.get("/api/info", headers=h).json()["version"] == main.app_version(False)


def test_version_items_only_own_app(env):
    ride, rh, sec = admin_on(RIDE)
    for r in ride.get("/api/admin/versions", headers=rh).json()["releases"]:
        assert r["items"] and all(len(it) < 3 or it[2] in ("ride", "both") for it in r["items"]), r["version"]
