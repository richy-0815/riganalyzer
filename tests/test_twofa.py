import json
import os
import re
import time

import pyotp
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
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True},
        "olaf": {"hash": h, "apps": ["rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    import main
    return TestClient(main.app, base_url=HOST)


def login(c, user="bofh"):
    return c.post("/login", data={"user": user, "password": "geheim12345"}, headers=ORIGIN, follow_redirects=False)


def csrf(c):
    return {**ORIGIN, "x-ra-csrf": c.cookies.get("ra_csrf")}


def setup(c):
    """Admin richtet 2FA ein -> (secret, notfall-codes)."""
    page = c.get("/2fa-einrichten", headers=ORIGIN)
    assert page.status_code == 200 and "<svg" in page.text
    secret = re.search(r'data-secret="([A-Z2-7]+)"', page.text).group(1)
    r = c.post("/2fa-einrichten", data={"code": pyotp.TOTP(secret).now()}, headers=ORIGIN)
    assert r.status_code == 200
    codes = re.findall(r'<li class="bk">([a-z0-9-]+)</li>', r.text)
    return secret, codes


def test_admin_without_2fa_must_set_up_first(env):
    login(env)
    assert env.get("/api/admin/users", headers=ORIGIN).status_code == 403
    r = env.get("/admin", headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/2fa-einrichten")


def test_setup_then_admin_works_and_8_backup_codes(env):
    login(env)
    secret, codes = setup(env)
    assert len(codes) == 8
    assert env.get("/api/admin/users", headers=ORIGIN).status_code == 200


def test_login_with_2fa_needs_code(env):
    login(env)
    secret, _ = setup(env)
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app, base_url=HOST)
    r = login(c)
    assert r.status_code == 303 and r.headers["location"].startswith("/zweiter-faktor")
    assert c.get("/api/me", headers=ORIGIN).status_code == 401          # noch nicht angemeldet
    bad = c.post("/zweiter-faktor", data={"code": "000000"}, headers=ORIGIN, follow_redirects=False)
    assert bad.status_code == 401
    ok = c.post("/zweiter-faktor", data={"code": pyotp.TOTP(secret).at(time.time() + 30)}, headers=ORIGIN,
                follow_redirects=False)   # naechster Zeitschritt: der vom Einrichten ist verbraucht
    assert ok.status_code == 303
    assert c.get("/api/admin/users", headers=ORIGIN).status_code == 200


def test_code_cannot_be_reused(env):
    login(env)
    secret, _ = setup(env)
    from fastapi.testclient import TestClient
    import main
    code = pyotp.TOTP(secret).at(time.time() + 30)
    a = TestClient(main.app, base_url=HOST)
    login(a)
    assert a.post("/zweiter-faktor", data={"code": code}, headers=ORIGIN, follow_redirects=False).status_code == 303
    b = TestClient(main.app, base_url=HOST)
    login(b)
    assert b.post("/zweiter-faktor", data={"code": code}, headers=ORIGIN, follow_redirects=False).status_code == 401


def test_backup_code_once(env):
    login(env)
    _, codes = setup(env)
    from fastapi.testclient import TestClient
    import main
    a = TestClient(main.app, base_url=HOST)
    login(a)
    assert a.post("/zweiter-faktor", data={"code": codes[0]}, headers=ORIGIN, follow_redirects=False).status_code == 303
    b = TestClient(main.app, base_url=HOST)
    login(b)
    assert b.post("/zweiter-faktor", data={"code": codes[0]}, headers=ORIGIN, follow_redirects=False).status_code == 401


def test_basic_auth_gives_no_admin(env):
    import base64
    login(env)
    setup(env)
    from fastapi.testclient import TestClient
    import main
    h = {"user-agent": "okhttp/5", "authorization": "Basic " + base64.b64encode(b"bofh:geheim12345").decode()}
    assert TestClient(main.app, base_url=HOST).get("/api/admin/users", headers=h).status_code == 404


def test_non_admin_login_unchanged(env):
    r = login(env, "olaf")
    assert r.status_code == 303 and not r.headers["location"].startswith("/zweiter-faktor")


def test_emergency_reset_cli(env):
    login(env)
    setup(env)
    import users
    users.main(["users.py", "2fa", "bofh", "aus"])
    u = json.loads(open(auth.USERS_FILE).read())["users"]["bofh"]
    assert "totp_secret" not in u and "totp_backup" not in u


def test_disabling_2fa_invalidates_admin_session(env):
    login(env)
    setup(env)
    assert env.get("/api/admin/users", headers=ORIGIN).status_code == 200
    import users
    users.main(["users.py", "2fa", "bofh", "aus"])
    assert env.get("/api/admin/users", headers=ORIGIN).status_code == 403


def test_code_attempts_are_throttled(env):
    login(env)
    setup(env)
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app, base_url=HOST)
    login(c)
    codes = [c.post("/zweiter-faktor", data={"code": "000000"}, headers=ORIGIN, follow_redirects=False).status_code
             for _ in range(12)]
    assert 429 in codes
