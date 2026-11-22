"""Gruppen-Admins (seit v0.14.0): bofh (Oberadmin) legt Gruppen an; ein Gruppen-Admin verwaltet nur Logins seiner
Gruppen, deren Apps in seinen Admin-Apps liegen – keine Admins, keine Gruppen, keine RigNavi-Positionen."""
import json

import pytest

import auth

HOST = "https://rideanalyzer.petrick.top"
ORIGIN = {"origin": HOST, "user-agent": "Mozilla/5.0"}


@pytest.fixture
def env(data_dir, monkeypatch):
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True},
        "gina": {"hash": h, "apps": ["ride", "rig"], "admin": {"apps": ["ride", "rig"], "groups": ["g1"]}},
        "rolf": {"hash": h, "apps": ["ride"], "admin": {"apps": ["ride"], "groups": ["g1"]}},
        "rita": {"hash": h, "apps": ["ride"], "admin": "ride"},                 # altes Format: admin als App-Name
        "otto": {"hash": h, "apps": ["ride"], "group": "g1"},
        "mia": {"hash": h, "apps": ["ride", "rig"], "group": "g1"},
        "olaf": {"hash": h, "apps": ["ride", "rig"], "group": "g2"},   # Tests laufen auf dem Ride-Host
        "kurt": {"hash": h, "apps": ["ride"]}}}))
    (data_dir / "groups.json").write_text(json.dumps({"groups": {"g1": {"name": "XJR-Club"}, "g2": {"name": "Womo"}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    return data_dir


def login(user="bofh", host=HOST):
    from fastapi.testclient import TestClient
    from conftest import enable_admin_2fa
    import main
    origin = {**ORIGIN, "origin": host}
    c = TestClient(main.app, base_url=host)
    r = c.post("/login", data={"user": user, "password": "geheim12345"}, headers=origin, follow_redirects=False)
    assert r.status_code == 303, r.text
    enable_admin_2fa(c, origin)
    return c, {**origin, "x-ra-csrf": c.cookies.get("ra_csrf")}


def names(c, h):
    return [u["name"] for u in c.get("/api/admin/users", headers=h).json()]


def test_scope():
    assert auth.admin_scope({"admin": True}) == "all"
    assert auth.admin_scope({"admin": "ride"}) == {"apps": ["ride"], "groups": []}
    assert auth.admin_scope({"admin": {"apps": ["rig", "x"], "groups": ["g1"]}}) == {"apps": ["rig"], "groups": ["g1"]}
    assert auth.admin_scope({}) is None and auth.admin_scope({"admin": False}) is None


def test_group_admin_sees_own_group_within_apps(env):
    c, h = login("gina")
    assert names(c, h) == ["mia", "otto"]
    act = c.get("/api/admin/activity", headers=h).json()
    assert [u["name"] for u in act["users"]] == ["mia", "otto"] and all(u["navi"] is None for u in act["users"])
    assert c.get("/api/me", headers=h).json()["admin"] == {"apps": ["ride", "rig"], "groups": ["g1"]}
    assert c.get("/api/admin/groups", headers=h).json() == [{"id": "g1", "name": "XJR-Club", "members": 2}]
    c, h = login("rolf")
    assert names(c, h) == ["otto"]                                   # mia hat auch Rig
    c, h = login("rita")
    assert names(c, h) == []                                         # alter Ride-Admin ohne Gruppe


def test_group_admin_cannot_touch_others(env):
    c, h = login("gina")
    for name in ("olaf", "kurt", "bofh", "rolf", "gina"):
        assert c.patch(f"/api/admin/users/{name}", json={"mail": "x@example.org"}, headers=h).status_code == 404
        assert c.post(f"/api/admin/users/{name}/password", json={}, headers=h).status_code == 404
        assert c.delete(f"/api/admin/users/{name}", headers=h).status_code == 404


def test_group_admin_manages_member(env):
    c, h = login("gina")
    assert c.patch("/api/admin/users/mia", json={"mail": "mia@example.org", "apps": ["ride"], "locked": True}, headers=h).status_code == 200
    for body in ({"admin": True}, {"admin": {"apps": ["ride"], "groups": ["g1"]}}, {"group": "g2"}, {"group": None}):
        assert c.patch("/api/admin/users/mia", json=body, headers=h).status_code == 403, body
    assert len(c.post("/api/admin/users/otto/password", json={}, headers=h).json()["password"]) == 14
    assert c.delete("/api/admin/users/otto", headers=h).status_code == 204
    c, h = login("rolf")
    assert c.patch("/api/admin/users/kurt", json={"mail": "k@example.org"}, headers=h).status_code == 404   # ohne Gruppe: nicht seins


def test_group_admin_creates_in_own_group(env):
    c, h = login("gina")
    assert c.post("/api/admin/users", json={"name": "finn", "group": "g2"}, headers=h).status_code == 403
    assert c.post("/api/admin/users", json={"name": "finn", "admin": True}, headers=h).status_code == 403
    assert c.post("/api/admin/users", json={"name": "finn", "apps": ["rig"]}, headers=h).status_code == 200
    finn = auth.read_disk()["finn"]
    assert finn["group"] == "g1" and finn["apps"] == ["ride"] and not finn.get("admin")      # App der Seite
    c, h = login("rolf")
    assert c.post("/api/admin/users", json={"name": "ben"}, headers=h).status_code == 200
    assert auth.read_disk()["ben"]["apps"] == ["ride"]


def test_only_oberadmin_manages_groups(env):
    c, h = login("gina")
    assert c.post("/api/admin/groups", json={"name": "Neu"}, headers=h).status_code == 403
    assert c.patch("/api/admin/groups/g1", json={"name": "X"}, headers=h).status_code == 403
    assert c.delete("/api/admin/groups/g1", headers=h).status_code == 403
    c, h = login()
    gid = c.post("/api/admin/groups", json={"name": "Alpen"}, headers=h).json()["id"]
    assert c.post("/api/admin/groups", json={"name": "alpen"}, headers=h).status_code == 422      # doppelt
    assert c.patch(f"/api/admin/groups/{gid}", json={"name": "Alpenpässe"}, headers=h).status_code == 200
    assert {g["name"] for g in c.get("/api/admin/groups", headers=h).json()} == {"XJR-Club", "Womo", "Alpenpässe"}
    assert c.delete("/api/admin/groups/g1", headers=h).status_code == 204
    d = auth.read_disk()
    assert "group" not in d["otto"] and "group" not in d["mia"]
    assert d["gina"]["admin"] == {"apps": ["ride", "rig"], "groups": []}


def test_oberadmin_sets_group_and_rights(env):
    c, h = login()
    assert c.patch("/api/admin/users/kurt", json={"group": "g2"}, headers=h).json()["group"] == "g2"
    assert c.patch("/api/admin/users/kurt", json={"group": "gx"}, headers=h).status_code == 422
    assert c.patch("/api/admin/users/kurt", json={"group": None}, headers=h).json()["group"] is None
    r = c.patch("/api/admin/users/olaf", json={"admin": {"apps": ["ride"], "groups": ["g2"]}}, headers=h)
    assert r.status_code == 200 and r.json()["admin"] == {"apps": ["ride"], "groups": ["g2"]}
    assert c.patch("/api/admin/users/olaf", json={"admin": {"apps": [], "groups": ["g2"]}}, headers=h).status_code == 422
    assert c.patch("/api/admin/users/olaf", json={"admin": {"apps": ["ride"], "groups": ["gx"]}}, headers=h).status_code == 422
    assert c.post("/api/admin/users", json={"name": "lea", "group": "g2"}, headers=h).status_code == 200
    users = {u["name"]: u for u in c.get("/api/admin/users", headers=h).json()}
    assert users["bofh"]["admin"] is True and users["kurt"]["admin"] is False and users["lea"]["group"] == "g2"
    assert users["rita"]["admin"] == {"apps": ["ride"], "groups": []}


def test_group_admin_is_not_last_admin(env):
    c, h = login()
    assert c.patch("/api/admin/users/bofh", json={"admin": {"apps": ["ride"], "groups": ["g1"]}}, headers=h).status_code == 409


def test_group_admin_versions_without_server_internals(env):
    """0.38.4: Gruppen-Admins sehen bei „Versionen“ nur den App-Stand und die Änderungen – keine Server-Interna."""
    c, h = login("gina")
    v = c.get("/api/admin/versions", headers=h).json()
    assert set(v["running"]) == {"server", "stand"}, v["running"]
    assert v["releases"]
    c, h = login("bofh")
    assert {"valhalla", "osm", "python", "regionen", "strassen"} <= set(c.get("/api/admin/versions", headers=h).json()["running"])
