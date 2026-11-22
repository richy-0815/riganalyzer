"""Sicherheits-Funde aus Ticket #16 (Review 08.11.2026), behoben in 0.40.2."""
import base64
import os

import pytest

import admin
import auth
import register
import reviews
from test_group_admin import HOST, ORIGIN, env, login  # noqa: F401  (Fixture env)

LANG = "ä" * 40   # 80 Byte, ueber der bcrypt-Grenze von 72


def _basic(user, pw):
    return {"user-agent": "okhttp/5", "authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def _client(host=HOST):
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app, base_url=host)


RIG = "https://riganalyzer.petrick.top"   # Bewertungen gibt es nur im RigAnalyzer (Rig-Router)


# ---------------------------------------------------------------- bcrypt 5: Passwoerter ueber 72 Byte

def test_langes_passwort_beim_login_kein_500(env):  # noqa: F811 – Fixture (Import oben)
    c = _client()
    r = c.post("/login", data={"user": "otto", "password": LANG}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code != 500
    r2 = c.post("/login", data={"user": "gibtsnicht", "password": LANG}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == r2.status_code              # gleiche Antwort – verraet nicht, dass es otto gibt
    assert c.get("/api/me", headers=_basic("otto", LANG)).status_code == 401
    assert c.get("/api/me", headers=_basic("otto", "geheim12345")).status_code == 200


def test_langes_passwort_wird_gezaehlt(env):  # noqa: F811 – Fixture (Import oben)
    c = _client()
    for _ in range(auth.MAX_FAILS):
        c.get("/api/me", headers=_basic("otto", LANG))
    assert c.get("/api/me", headers=_basic("otto", "geheim12345")).status_code == 429


def test_alter_hash_mit_langem_passwort_passt_weiter():
    """Fruehere bcrypt-Versionen kuerzten still auf 72 Byte – solche Hashes muessen weiter passen."""
    import bcrypt
    lang = "x" * 100
    h = bcrypt.hashpw(lang.encode()[:72], bcrypt.gensalt(4)).decode()
    assert bcrypt.checkpw(auth._pwb(lang), h.encode())


def test_neues_passwort_ueber_72_byte_klare_meldung(env):  # noqa: F811 – Fixture (Import oben)
    assert "höchstens 72 Byte" in admin.pw_problem(LANG)
    assert admin.pw_problem("a" * 72) is None and admin.pw_problem("a" * 73)
    with pytest.raises(admin.AdminError, match="72 Byte"):
        admin.set_own_password("otto", LANG)
    with pytest.raises(register.RegisterError, match="72 Byte"):
        register.create("neuling", "n@example.org", LANG, LANG, "", "ride")
    with pytest.raises(ValueError):
        auth.hash_pw(LANG)


# ---------------------------------------------------------------- Passwort aendern: Bremse

def test_passwort_aendern_wird_gebremst(env):  # noqa: F811 – Fixture (Import oben)
    from test_admin_0141 import plain_login
    c = plain_login("otto")
    h = {**ORIGIN, "x-ra-csrf": c.cookies.get("ra_csrf")}
    for _ in range(auth.MAX_FAILS):
        assert c.post("/api/me/password", json={"old": "falsch1234", "new": "ganzneu12345"}, headers=h).status_code == 403
    r = c.post("/api/me/password", json={"old": "geheim12345", "new": "ganzneu12345"}, headers=h)
    assert r.status_code == 429


# ---------------------------------------------------------------- Fremde Bewertungen nur Oberadmin mit 2FA

@pytest.fixture
def rev(tmp_path, monkeypatch):
    monkeypatch.setattr(reviews, "FILE", str(tmp_path / "reviews.sqlite"))
    reviews.save("otto", {"key": "node/5", "lat": 45.0, "lon": 5.0, "stars": 2})


def test_gruppenadmin_darf_fremde_bewertung_nicht_loeschen(env, rev):  # noqa: F811 – Fixture (Import oben)
    c, h = login("gina", RIG)                               # Gruppen-Admin mit 2FA
    assert c.delete("/api/reviews", params={"key": "node/5", "user": "otto"}, headers=h).status_code == 403
    c2 = _client(RIG)                                       # Oberadmin per Basic-Auth (ohne 2FA)
    assert c2.delete("/api/reviews", params={"key": "node/5", "user": "otto"}, headers=_basic("bofh", "geheim12345")).status_code == 403
    assert reviews.for_place("node/5")


def test_oberadmin_mit_2fa_darf(env, rev):  # noqa: F811 – Fixture (Import oben)
    c, h = login("bofh", RIG)
    assert c.delete("/api/reviews", params={"key": "node/5", "user": "otto"}, headers=h).json()["deleted"] == 1


# ---------------------------------------------------------------- Rest

def test_admin_log_ist_privat(env):  # noqa: F811 – Fixture (Import oben)
    admin.record("bofh", "otto", "Test")
    assert oct(os.stat(admin._log_file()).st_mode & 0o777) == "0o600"


def test_abmelden_loescht_auch_2fa_und_csrf(env):  # noqa: F811 – Fixture (Import oben)
    c, _ = login("bofh")
    r = c.get("/logout", headers=ORIGIN, follow_redirects=False)
    gone = " ".join(v for k, v in r.headers.multi_items() if k == "set-cookie")
    for name in (auth.COOKIE, "ra_mfa", auth.CSRF_COOKIE):
        assert f"{name}=" in gone and "Max-Age=0" in gone, name


def test_hoechstens_drei_analysen_je_login(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    import main
    from fastapi import HTTPException
    monkeypatch.setattr(main, "jobs", {f"j{i}": {"state": "running", "by": "otto@1.2.3.4", "t": __import__("time").time()} for i in range(3)})
    with pytest.raises(HTTPException) as e:
        main._submit(b"<gpx/>", "x.gpx", by="otto@1.2.3.4")
    assert e.value.status_code == 429 and "3 Analysen" in e.value.detail


def test_fails_werden_abgeraeumt(monkeypatch):
    monkeypatch.setattr(auth, "_fails", {f"10.0.{i // 250}.{i % 250}": [0.0] for i in range(2100)})
    auth.throttled("1.1.1.1")   # zaehlt (und raeumt) throttled(), failed() behaelt nur den Versuch
    assert len(auth._fails) == 1
