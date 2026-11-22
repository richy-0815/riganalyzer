import json
import os
import time

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import admin
import auth
import mailer
import reset

HOST = "https://riganalyzer.petrick.top"
ORIGIN = {"origin": HOST, "user-agent": "Mozilla/5.0"}


@pytest.fixture
def mails(data_dir, monkeypatch):
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True, "mail": "bofh@example.org"},
        "olaf": {"hash": h, "apps": ["rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    reset._ip_hits.clear()
    sent = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda msg: sent.append(msg))
    return sent


def _token(msg):
    body = msg.get_content()
    return body.split("passwort-neu?t=")[1].split()[0]


def test_unknown_and_no_mail_send_nothing(mails):
    reset.request("niemand", HOST)
    reset.request("olaf", HOST)
    assert mails == []


def test_known_with_mail_gets_link_by_name_or_mail(mails):
    reset.request("bofh", HOST)
    reset.request("BOFH@example.org", HOST)
    assert len(mails) == 2 and mails[0]["To"] == "bofh@example.org"
    assert f"{HOST}/passwort-neu?t=" in mails[0].get_content()
    assert "RigAnalyzer" in mails[0]["From"]


def test_rate_limit_three_per_hour(mails):
    for _ in range(4):
        reset.request("bofh", HOST)
    assert len(mails) == 3


def test_consume_once(mails):
    reset.request("bofh", HOST)
    t = _token(mails[0])
    assert reset.consume(t, "ganzneu12345") == ("bofh", False)
    assert auth.check("bofh", "ganzneu12345", "rig")
    with pytest.raises(reset.Invalid):
        reset.consume(t, "nochmalneu123")


def test_expired(mails, monkeypatch):
    reset.request("bofh", HOST)
    t = _token(mails[0])
    real = time.time
    monkeypatch.setattr(reset.time, "time", lambda: real() + 61 * 60)
    with pytest.raises(reset.Invalid):
        reset.consume(t, "ganzneu12345")


def test_admin_password_change_invalidates_token(mails):
    reset.request("bofh", HOST)
    t = _token(mails[0])
    admin.set_password("bofh", "vomadmin12345")
    with pytest.raises(reset.Invalid):
        reset.consume(t, "ganzneu12345")


def test_weak_password_rejected(mails):
    reset.request("bofh", HOST)
    with pytest.raises(admin.AdminError):
        reset.consume(_token(mails[0]), "kurz")


def test_smtp_failure_returns_false(monkeypatch):
    def boom(msg):
        raise OSError("Verbindung abgelehnt")
    monkeypatch.setattr(mailer, "_smtp_send", boom)
    assert mailer.send("a@b.de", "x", "y") is False


@pytest.fixture
def client(mails):
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app, base_url=HOST)


def test_forgot_page_same_answer_and_link_uses_fixed_host(client, mails):
    # ohne Origin (sonst lehnt die Origin-Pruefung den fremden Host schon vorher ab)
    a = client.post("/passwort-vergessen", data={"who": "niemand"}, headers={"host": "evil.example"})
    b = client.post("/passwort-vergessen", data={"who": "bofh"}, headers={"host": "riganalyzer.evil.example"})
    assert a.status_code == b.status_code == 200
    assert "Falls es den Login gibt" in a.text and "Falls es den Login gibt" in b.text
    for _ in range(50):   # Versand laeuft im Hintergrund
        if mails:
            break
        time.sleep(0.05)
    assert len(mails) == 1 and "https://riganalyzer.petrick.top/passwort-neu?t=" in mails[0].get_content()


def test_reset_page_flow(client, mails):
    reset.request("bofh", HOST)
    t = _token(mails[0])
    r = client.post("/passwort-neu", data={"t": t, "pw": "ganzneu12345", "pw2": "ganzneu12345"}, headers=ORIGIN)
    assert r.status_code == 200 and "gespeichert" in r.text
    r2 = client.post("/passwort-neu", data={"t": t, "pw": "ganzneu12345", "pw2": "ganzneu12345"}, headers=ORIGIN)
    assert "ungültig oder abgelaufen" in r2.text


def _login(c, user="bofh", pw="geheim12345"):
    c.post("/login", data={"user": user, "password": pw}, headers=ORIGIN, follow_redirects=False)
    if auth.is_admin(user):
        from conftest import enable_admin_2fa
        enable_admin_2fa(c, ORIGIN)
    return {**ORIGIN, "x-ra-csrf": c.cookies.get("ra_csrf")}


def test_me_password_needs_old_and_csrf_and_keeps_session(client):
    h = _login(client)
    assert client.post("/api/me/password", json={"old": "falsch", "new": "ganzneu12345"}, headers=h).status_code == 403
    assert client.post("/api/me/password", json={"old": "geheim12345", "new": "ganzneu12345"},
                       headers={**h, "x-ra-csrf": "x"}).status_code == 403
    assert client.post("/api/me/password", json={"old": "geheim12345", "new": "ganzneu12345"}, headers=h).status_code == 200
    assert client.get("/api/me", headers=ORIGIN).json()["user"] == "bofh"   # neues Cookie, weiter angemeldet


def test_admin_create_with_mail_failure_still_returns_password(client, monkeypatch):
    def boom(msg):
        raise OSError("weg")
    monkeypatch.setattr(mailer, "_smtp_send", boom)
    h = _login(client)
    r = client.post("/api/admin/users", json={"name": "finn", "mail": "finn@example.org", "send_mail": True}, headers=h).json()
    assert len(r["password"]) == 14 and r["mail_ok"] is False


def test_forgot_answer_does_not_wait_for_mail(client, monkeypatch):
    import time as _t
    monkeypatch.setattr(mailer, "_smtp_send", lambda msg: _t.sleep(2))
    t0 = _t.time()
    r = client.post("/passwort-vergessen", data={"who": "bofh"}, headers=ORIGIN)
    assert r.status_code == 200 and _t.time() - t0 < 1.0


def test_file_is_private(mails, data_dir):
    """reset.json enthaelt Passwort-Hashes -> nur fuer den Besitzer lesbar (wie users.json)."""
    import stat
    reset.request("bofh", HOST)
    assert stat.S_IMODE(os.stat(os.path.join(str(data_dir), "reset.json")).st_mode) == 0o600
