import base64
import json
import logging
import os

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import auth

HOST = "https://riganalyzer.petrick.top"
ORIGIN = {"origin": HOST, "user-agent": "Mozilla/5.0"}


@pytest.fixture
def env(data_dir, monkeypatch):
    from fastapi.testclient import TestClient

    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True, "mail": "bofh@example.org"},
        "olaf": {"hash": h, "apps": ["rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    import main
    return TestClient(main.app, base_url=HOST)


def login(c, user="bofh", pw="geheim12345"):
    r = c.post("/login", data={"user": user, "password": pw}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303, r.text
    if auth.is_admin(user):
        from conftest import enable_admin_2fa
        enable_admin_2fa(c, ORIGIN)
    return {**ORIGIN, "x-ra-csrf": c.cookies.get("ra_csrf")}


def basic(user, pw="geheim12345"):
    return {"user-agent": "okhttp/5", "authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_non_admin_sees_404(env):
    h = login(env, "olaf")
    assert env.get("/api/admin/users", headers=h).status_code == 404


def test_admin_lists_users_without_hashes(env):
    h = login(env)
    r = env.get("/api/admin/users", headers=h).json()
    assert {u["name"] for u in r} == {"bofh", "olaf"} and all("hash" not in u for u in r)
    assert next(u for u in r if u["name"] == "bofh")["last_login"]


def test_changes_need_csrf_and_cookie(env):
    h = login(env)
    assert env.post("/api/admin/users", json={"name": "finn"}, headers={**h, "x-ra-csrf": "falsch"}).status_code == 403
    assert env.post("/api/admin/users", json={"name": "finn"}, headers={**h, "origin": "https://evil.example"}).status_code == 403
    assert env.post("/api/admin/users", json={"name": "finn"}, headers=basic("bofh")).status_code == 403


def test_create_set_password_and_login(env, caplog):
    h = login(env)
    r = env.post("/api/admin/users", json={"name": "finn", "mail": "finn@example.org", "apps": ["rig"]}, headers=h).json()
    assert len(r["password"]) == 14
    assert env.post("/api/admin/users/finn/password", json={"password": "kurz"}, headers=h).status_code == 422
    with caplog.at_level(logging.INFO, logger="zugriff"):
        r2 = env.post("/api/admin/users/finn/password", json={}, headers=h).json()
    assert len(r2["password"]) == 14 and r2["password"] not in caplog.text
    assert "bofh setzt Passwort von finn" in caplog.text
    assert auth.check("finn", r2["password"], "rig")


def test_last_admin_protected(env):
    h = login(env)
    for body in ({"admin": False}, {"locked": True}):
        r = env.patch("/api/admin/users/bofh", json=body, headers=h)
        assert r.status_code == 409 and "letzte Admin" in r.json()["detail"]
    assert env.delete("/api/admin/users/bofh", headers=h).status_code == 409
    env.patch("/api/admin/users/olaf", json={"admin": True}, headers=h)
    assert env.patch("/api/admin/users/bofh", json={"admin": False}, headers=h).status_code == 200


def test_locked_user_is_out_immediately(env):
    from fastapi.testclient import TestClient
    import main
    olaf = TestClient(main.app, base_url=HOST)
    login(olaf, "olaf")
    assert olaf.get("/api/me", headers=ORIGIN).json()["user"] == "olaf"
    h = login(env)
    assert env.patch("/api/admin/users/olaf", json={"locked": True}, headers=h).status_code == 200
    assert olaf.get("/api/me", headers=ORIGIN).status_code == 401
    assert TestClient(main.app, base_url=HOST).get("/api/trips", headers=basic("olaf")).status_code == 401


def test_password_change_kills_old_cookie(env):
    from fastapi.testclient import TestClient
    import main
    olaf = TestClient(main.app, base_url=HOST)
    login(olaf, "olaf")
    h = login(env)
    env.post("/api/admin/users/olaf/password", json={}, headers=h)
    assert olaf.get("/api/me", headers=ORIGIN).status_code == 401


def test_me_has_admin_flag(env):
    login(env)
    me = env.get("/api/me", headers=ORIGIN).json()
    assert me["admin"] is True and me["mail"] == "bofh@example.org"


def test_users_cli_admin_flag(data_dir, monkeypatch):
    import users
    (data_dir / "users.json").write_text(json.dumps({"users": {"bofh": {"hash": "x", "apps": ["rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    auth._users["mtime"] = None
    users.main(["users.py", "admin", "bofh", "an"])
    users.main(["users.py", "mail", "bofh", "bofh@example.org"])
    u = json.loads((data_dir / "users.json").read_text())["users"]["bofh"]
    assert u["admin"] is True and u["mail"] == "bofh@example.org"


def test_concurrent_logins_and_admin_changes_keep_file_and_changes(env):
    import threading
    import admin
    for i in range(10):
        admin.create(f"user{i}", apps=["rig"])
    errs = []

    def logins():
        for _ in range(40):
            try:
                for i in range(10):
                    auth.touch_login(f"user{i}", force=True)
            except Exception as e:  # noqa: BLE001
                errs.append(e)

    def locks():
        for i in range(10):
            try:
                admin.update(f"user{i}", locked=True)
            except Exception as e:  # noqa: BLE001
                errs.append(e)
    th = [threading.Thread(target=logins) for _ in range(3)] + [threading.Thread(target=locks)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert errs == []
    data = json.loads((auth.USERS_FILE and open(auth.USERS_FILE).read()))["users"]
    assert all(data[f"user{i}"].get("locked") for i in range(10))


def test_touch_login_skips_recent(env):
    auth.touch_login("olaf", force=True)
    m = os.path.getmtime(auth.USERS_FILE)
    auth.touch_login("olaf")          # < 10 min seit letztem Eintrag -> nicht schreiben
    assert os.path.getmtime(auth.USERS_FILE) == m
