"""Mailwechsel im Konto (0.37.6): Passwort + Link, Bestaetigungsseite, Abbrechen, Admin-Override."""
import json
import logging
import os

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import auth
import mailchange
import mailer

HOST = "https://riganalyzer.petrick.top"
ORIGIN = {"origin": HOST, "user-agent": "Mozilla/5.0"}
NEW = "neu@example.org"
PW = "geheim12345"


@pytest.fixture
def mails(data_dir, monkeypatch):
    h = auth.hash_pw(PW)
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True, "mail": "bofh@example.org"},
        "olaf": {"hash": h, "apps": ["rig"], "mail": "olaf@example.org"}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    monkeypatch.delenv("MAIL_OFF", raising=False)
    auth._users["mtime"] = None
    auth._fails.clear()
    sent = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda msg: sent.append(msg))
    return sent


@pytest.fixture
def client(mails):
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app, base_url=HOST)


def _login(c, user="olaf", pw=PW):
    c.post("/login", data={"user": user, "password": pw}, headers=ORIGIN, follow_redirects=False)
    if auth.is_admin(user):
        from conftest import enable_admin_2fa
        enable_admin_2fa(c, ORIGIN)
    return {**ORIGIN, "x-ra-csrf": c.cookies.get("ra_csrf")}


def _link(mails):
    return next(m for m in mails if m["To"] == NEW).get_content().split("mail-bestaetigen?t=")[1].split()[0]


def _me(c):
    return c.get("/api/me", headers=ORIGIN).json()


def test_needs_password(client, mails):
    h = _login(client)
    r = client.post("/api/me/mail", json={"mail": NEW, "password": "falsch"}, headers=h)
    assert r.status_code == 403 and "Passwort" in r.json()["detail"]
    assert mails == [] and _me(client)["mail_pending"] is None


def test_needs_csrf(client, mails):
    h = _login(client)
    r = client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers={**h, "x-ra-csrf": "x"})
    assert r.status_code == 403 and mails == []


def test_request_then_confirm_via_post_only(client, mails):
    h = _login(client)
    r = client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h).json()
    assert r["status"] == "sent" and r["pending"] == NEW and "t=" not in json.dumps(r)
    assert _me(client)["mail_pending"]["mail"] == NEW
    t = _link(mails)
    page = client.get(f"/mail-bestaetigen?t={t}")
    assert page.status_code == 200 and "bestätigen" in page.text and t in page.text
    assert _me(client)["mail"] == "olaf@example.org"            # GET allein verbraucht nichts (Mail-Scanner)
    done = client.post("/mail-bestaetigen", data={"t": t}, headers=ORIGIN)
    assert "Mailadresse bestätigt" in done.text
    me = _me(client)
    assert me["mail"] == NEW and me["mail_pending"] is None


def test_confirm_works_logged_out(client, mails):
    from fastapi.testclient import TestClient
    import main
    h = _login(client)
    client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h)
    anon = TestClient(main.app, base_url=HOST)
    assert "Mailadresse bestätigt" in anon.post("/mail-bestaetigen", data={"t": _link(mails)}, headers=ORIGIN).text
    assert auth.users()["olaf"]["mail"] == NEW


def test_bad_token_page(client):
    r = client.post("/mail-bestaetigen", data={"t": "quatsch"}, headers=ORIGIN)
    assert r.status_code == 200 and "ungültig oder abgelaufen" in r.text


def test_cancel(client, mails):
    h = _login(client)
    client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h)
    assert client.delete("/api/me/mail", headers=h).json() == {"ok": True}
    assert _me(client)["mail_pending"] is None
    assert "ungültig" in client.post("/mail-bestaetigen", data={"t": _link(mails)}, headers=ORIGIN).text


def test_unchanged(client, mails):
    h = _login(client)
    r = client.post("/api/me/mail", json={"mail": "OLAF@example.org", "password": PW}, headers=h).json()
    assert r == {"status": "unchanged"} and mails == []


def test_remove_with_empty_mail(client, mails):
    h = _login(client)
    assert client.post("/api/me/mail", json={"mail": "", "password": PW}, headers=h).json() == {"status": "removed"}
    assert _me(client)["mail"] == ""


def test_invalid_address_422(client, mails):
    h = _login(client)
    assert client.post("/api/me/mail", json={"mail": "kein-at", "password": PW}, headers=h).status_code == 422


def test_send_failure_502(client, monkeypatch):
    def boom(msg):
        raise OSError("weg")
    h = _login(client)
    monkeypatch.setattr(mailer, "_smtp_send", boom)
    r = client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h)
    assert r.status_code == 502 and _me(client)["mail_pending"] is None


def test_rate_limit_429(client, mails):
    h = _login(client)
    for _ in range(3):
        client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h)
    assert client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h).status_code == 429


def test_admin_update_drops_pending(client, mails):
    h = _login(client)
    client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h)
    t = _link(mails)
    from fastapi.testclient import TestClient
    import main
    adm = TestClient(main.app, base_url=HOST)
    ha = _login(adm, "bofh")
    assert adm.patch("/api/admin/users/olaf", json={"mail": "vomadmin@example.org"}, headers=ha).status_code == 200
    assert mailchange.pending("olaf") is None
    with pytest.raises(mailchange.Invalid):
        mailchange.confirm(t)
    assert auth.users()["olaf"]["mail"] == "vomadmin@example.org"


def test_wrong_password_counts_for_lockout(client, mails):
    h = _login(client)
    for _ in range(auth.MAX_FAILS):
        client.post("/api/me/mail", json={"mail": NEW, "password": "falsch"}, headers=h)
    assert client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h).status_code == 429


def test_no_token_or_address_in_access_log(client, mails, caplog):
    h = _login(client)
    with caplog.at_level(logging.INFO, logger="zugriff"):
        client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h)
        t = _link(mails)
        client.post("/mail-bestaetigen", data={"t": t}, headers=ORIGIN)
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == "zugriff")
    assert "Mailwechsel" in text and t not in text and NEW not in text


def test_public_path():
    import main
    assert "/mail-bestaetigen" in main.PUBLIC


def test_confirm_while_logged_in_form_post_checks_origin(client, mails):
    """Angemeldet auf den Link klicken: Formular-POST ohne JS-Token muss gehen (Herkunft wird geprueft) –
    von fremder Herkunft nicht (CSRF, zentral seit 0.38.0)."""
    h = _login(client)
    client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h)
    t = _link(mails)
    evil = client.post("/mail-bestaetigen", data={"t": t}, headers={"origin": "https://evil.example", "user-agent": "Mozilla/5.0"})
    assert evil.status_code == 403 and _me(client)["mail"] == "olaf@example.org"
    ok = client.post("/mail-bestaetigen", data={"t": t}, headers=ORIGIN)
    assert ok.status_code == 200 and "Mailadresse bestätigt" in ok.text


def test_confirm_logged_in_browser_with_no_referrer_policy(client, mails):
    """Prod 08.11.2026: Traefik setzt Referrer-Policy no-referrer -> der Browser schickt beim Formular-POST
    „Origin: null“ und keinen Referer. Angemeldet bestaetigen muss trotzdem gehen (der Token im Formular ist das
    Geheimnis); eine erkennbar fremde Herkunft bleibt gesperrt."""
    h = _login(client)
    client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h)
    t = _link(mails)
    browser = {"origin": "null", "user-agent": "Mozilla/5.0"}
    r = client.post("/mail-bestaetigen", data={"t": t}, headers=browser)
    assert r.status_code == 200 and "Mailadresse bestätigt" in r.text


def test_confirm_still_rejects_foreign_origin_when_logged_in(client, mails):
    h = _login(client)
    client.post("/api/me/mail", json={"mail": NEW, "password": PW}, headers=h)
    r = client.post("/mail-bestaetigen", data={"t": _link(mails)},
                    headers={"origin": "https://evil.example", "user-agent": "Mozilla/5.0"})
    assert r.status_code == 403 and _me(client)["mail"] == "olaf@example.org"
