"""Startpasswort (seit v0.10.3): Admins sehen und bekommen per Mail mit, wenn ein Nutzer es selbst ersetzt.
Dazu: Auge am Passwortfeld, Android-App und Aenderungen getrennt je App."""
import json
import os

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import admin
import auth
import mailer
import reset

RIG = "https://riganalyzer.petrick.top"
RIDE = "https://rideanalyzer.petrick.top"
UA = {"user-agent": "Mozilla/5.0"}
REL = json.load(open(os.path.join(os.path.dirname(__file__), "..", "app", "releases.json"), encoding="utf-8"))


@pytest.fixture
def env(data_dir, monkeypatch):
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True, "mail": "bofh@example.org"},
        "olaf": {"hash": h, "apps": ["ride", "rig"], "mail": "olaf@example.org"}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    reset._ip_hits.clear()
    sent = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda msg: sent.append(msg))
    # Admin-Hinweis laeuft im Thread -> fuer den Test direkt ausfuehren
    import main
    monkeypatch.setattr(main, "_in_background", lambda fn, *a: fn(*a))
    return sent


def _client(host):
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app, base_url=host)


def _login(c, host, user="olaf", pw="geheim12345"):
    r = c.post("/login", data={"user": user, "password": pw}, headers={**UA, "origin": host}, follow_redirects=False)
    assert r.status_code == 303
    return {**UA, "origin": host, "x-ra-csrf": c.cookies.get("ra_csrf")}


def _admin_notes(sent):
    return [m for m in sent if "Startpasswort" in m["Subject"]]


def test_admin_set_password_marks_initial(env):
    admin.set_password("olaf", "vomadmin12345")
    u = next(x for x in admin.list_users() if x["name"] == "olaf")
    assert u["pw_initial"] is True and u["pw_changed"] is None
    assert admin.create("finn")
    assert next(x for x in admin.list_users() if x["name"] == "finn")["pw_initial"] is True


def test_own_change_of_initial_password_tells_admins(env):
    admin.set_password("olaf", "vomadmin12345")
    c = _client(RIG)
    h = _login(c, RIG, pw="vomadmin12345")
    r = c.post("/api/me/password", json={"old": "vomadmin12345", "new": "meinseigenes1"}, headers=h)
    assert r.status_code == 200
    u = next(x for x in admin.list_users() if x["name"] == "olaf")
    assert u["pw_initial"] is False and u["pw_changed"]
    notes = _admin_notes(env)
    assert len(notes) == 1 and notes[0]["To"] == "bofh@example.org" and "olaf" in notes[0]["Subject"]
    # zweiter eigener Wechsel: kein Startpasswort mehr -> keine Mail
    c.post("/api/me/password", json={"old": "meinseigenes1", "new": "nochmalanders1"}, headers=h)
    assert len(_admin_notes(env)) == 1


def test_reset_link_on_initial_password_tells_admins(env):
    admin.set_password("olaf", "vomadmin12345")
    reset.request("olaf", RIG)
    t = env[-1].get_content().split("passwort-neu?t=")[1].split()[0]
    c = _client(RIG)
    r = c.post("/passwort-neu", data={"t": t, "pw": "perlinkneu123", "pw2": "perlinkneu123"}, headers={**UA, "origin": RIG})
    assert "gespeichert" in r.text
    assert len(_admin_notes(env)) == 1


def test_password_fields_have_eye(env):
    c = _client(RIG)
    assert "pwtoggle.js" in c.get("/login", headers=UA).text
    assert c.get("/static/pwtoggle.js", headers=UA).status_code == 200   # auch ohne Anmeldung
    _login(c, RIG)
    assert "pwtoggle.js" in c.get("/konto", headers=UA).text


def test_android_app_only_on_rig(env):
    ride, rig = _client(RIDE), _client(RIG)
    _login(ride, RIDE)
    _login(rig, RIG)
    assert "/app.html" not in ride.get("/tech.html", headers=UA).text
    assert "/app.html" in rig.get("/tech.html", headers=UA).text
    r = ride.get("/app.html", headers=UA, follow_redirects=False)
    assert r.status_code == 404                      # Ride bleibt Ride, kein Sprung zum RigAnalyzer


def test_releases_split_by_app(env):
    for host, mine in ((RIDE, ("ride", "beide")), (RIG, ("rig", "beide"))):
        c = _client(host)
        _login(c, host)
        page = c.get("/releases.html", headers=UA).text
        for r in REL:
            if r.get("public"):
                assert (f'id="v{r["version"]}"' in page) == (r["app"] in mine), (host, r["version"])
