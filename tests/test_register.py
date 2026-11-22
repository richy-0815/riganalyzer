"""Registrieren mit Freischaltung (v0.20.0): gesperrter Login, Mail an Admins, Freischalten im Admin-Bereich."""
import json
import os

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import auth
import register

RIG = "https://riganalyzer.petrick.top"
RIDE = "https://rideanalyzer.petrick.top"
UA = {"user-agent": "Mozilla/5.0"}
FORM = {"user": "Neuling1", "mail": "neu@example.org", "pw": "langespasswort", "pw2": "langespasswort",
        "note": "Kumpel vom XJR-Treffen"}


@pytest.fixture
def env(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    import mailer
    import main

    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True, "mail": "bofh@example.org"}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    register._ip_hits.clear()
    register._ip_tries.clear()
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body, app=None: sent.append((to, subject, body, app)) or True)
    monkeypatch.setattr(main, "_in_background", lambda fn, *a: fn(*a))
    return TestClient(main.app, base_url=RIG), TestClient(main.app, base_url=RIDE), sent


def test_page_public_and_linked(env):
    rig, ride, _ = env
    for c in (rig, ride):
        assert c.get("/registrieren", headers=UA, follow_redirects=False).status_code == 200
        assert 'href="/registrieren"' in c.get("/login", headers=UA).text
        assert 'href="/registrieren"' in c.get("/info.html", headers=UA).text
        assert "Frag Richy" not in c.get("/login", headers=UA).text


def test_register_creates_locked_pending_login_and_mails_admin(env):
    rig, _, sent = env
    r = rig.post("/registrieren", data=FORM, headers=UA)
    assert r.status_code == 200 and "beantragt" in r.text
    u = auth.read_disk()["neuling1"]
    assert u["locked"] and u["pending"] and u["apps"] == ["rig"] and u["mail"] == "neu@example.org"
    assert u["note"] == "Kumpel vom XJR-Treffen" and not u.get("admin") and not u.get("pw_initial")
    assert auth.check("neuling1", "langespasswort", "rig") is False
    (to, subject, body, app), = sent
    assert to == "bofh@example.org" and "neuling1" in subject and "RigAnalyzer" in subject
    assert f"{RIG}/admin" in body and "Kumpel vom XJR-Treffen" in body and app == "rig"


def test_ride_page_gives_ride_only(env):
    _, ride, _ = env
    ride.post("/registrieren", data=FORM, headers=UA)
    assert auth.read_disk()["neuling1"]["apps"] == ["ride"]


def test_pending_login_says_so_only_with_right_password(env):
    rig, _, _ = env
    rig.post("/registrieren", data=FORM, headers=UA)
    r = rig.post("/login", data={"user": "neuling1", "password": "langespasswort"}, headers=UA, follow_redirects=False)
    assert r.status_code == 403 and "Freischaltung" in r.text
    r = rig.post("/login", data={"user": "neuling1", "password": "falschfalsch"}, headers=UA, follow_redirects=False)
    assert r.status_code == 401 and "Freischaltung" not in r.text


@pytest.mark.parametrize("change,err", [
    ({"user": "a b"}, "Benutzername"),
    ({"user": "bofh"}, "gibt es schon"),
    ({"mail": "kaputt"}, "Mailadresse"),
    ({"pw": "kurz", "pw2": "kurz"}, "mindestens"),
    ({"pw2": "anderespasswort"}, "verschieden"),
])
def test_validation(env, change, err):
    rig, _, sent = env
    r = rig.post("/registrieren", data={**FORM, **change}, headers=UA)
    assert r.status_code == 422 and err in r.text
    assert "neuling1" not in auth.read_disk() and not sent


def test_honeypot_does_nothing(env):
    rig, _, sent = env
    r = rig.post("/registrieren", data={**FORM, "website": "http://spam"}, headers=UA)
    assert r.status_code == 200 and "neuling1" not in auth.read_disk() and not sent


def test_ip_throttle(env):
    rig, _, _ = env
    for i in range(register.IP_MAX):
        assert rig.post("/registrieren", data={**FORM, "user": f"bot{i}", "mail": f"b{i}@x.de"}, headers=UA).status_code == 200
    assert rig.post("/registrieren", data={**FORM, "user": "botx", "mail": "bx@x.de"}, headers=UA).status_code == 429


def test_admin_approves_and_user_gets_mail(env):
    rig, _, sent = env
    rig.post("/registrieren", data=FORM, headers=UA)
    sent.clear()
    from test_admin import login
    h = login(rig)
    lst = rig.get("/api/admin/users", headers=h).json()
    me = next(u for u in lst if u["name"] == "neuling1")
    assert me["pending"] and me["locked"] and me["note"]
    r = rig.patch("/api/admin/users/neuling1", json={"locked": False}, headers=h)
    assert r.status_code == 200 and not r.json()["pending"] and not r.json()["locked"]
    u = auth.read_disk()["neuling1"]
    assert "pending" not in u and "note" not in u
    (to, subject, body, app), = sent
    assert to == "neu@example.org" and "freigeschaltet" in subject and f"{RIG}/login" in body
    assert rig.get("/api/admin/log", headers=h).json()[0]["what"] == "freigeschaltet"
    assert auth.check("neuling1", "langespasswort", "rig")
    # spaeter sperren/entsperren schickt keine Freischalt-Mail mehr
    rig.patch("/api/admin/users/neuling1", json={"locked": True}, headers=h)
    rig.patch("/api/admin/users/neuling1", json={"locked": False}, headers=h)
    assert len(sent) == 1
