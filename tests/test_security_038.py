"""Sicherheits-Release 0.38.0: zentrales CSRF, Body-Limit, serverseitiges Abmelden, Ueberall abmelden,
Dateirechte von users.json, gleiche Laufzeit fuer unbekannte Logins."""
import json
import os
import stat

import bcrypt
import pytest

import auth

HOST = "https://riganalyzer.petrick.top"
ORIGIN = {"origin": HOST, "user-agent": "Mozilla/5.0"}


@pytest.fixture
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    h = bcrypt.hashpw(b"geheim12345", bcrypt.gensalt(rounds=4)).decode()
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "finn": {"hash": h, "apps": ["ride", "rig"]},
    }}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    return TestClient(main.app, base_url=HOST)


def _login(c, **extra):
    r = c.post("/login", data={"user": "finn", "password": "geheim12345", **extra}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    return {**ORIGIN, "x-ra-csrf": c.cookies.get("ra_csrf")}


def test_mutating_api_without_token_is_rejected(client):
    h = _login(client)
    body = {"name": "Testtour", "points": []}
    no_tok = {k: v for k, v in h.items() if k != "x-ra-csrf"}
    assert client.post("/api/trips", json=body, headers=no_tok).status_code == 403
    assert client.post("/api/regions", json={"region": "europe/andorra"}, headers=no_tok).status_code == 403
    assert client.delete("/api/trips/abc123", headers=no_tok).status_code == 403
    # mit Token kommt die Anfrage durch (was der Endpunkt dann antwortet, ist hier egal – nur kein CSRF-403)
    r = client.delete("/api/trips/abc123", headers=h)
    assert r.status_code != 403 or "Sicherheitsprüfung" not in r.text


def test_foreign_origin_rejected_even_with_token(client):
    h = _login(client)
    r = client.post("/api/me/settings", json={"speed_warn": False}, headers={**h, "origin": "https://fremd.example"})
    assert r.status_code == 403


def test_form_post_from_foreign_origin_rejected(client):
    r = client.post("/login", data={"user": "finn", "password": "geheim12345"},
                    headers={"origin": "https://evil.example", "user-agent": "Mozilla/5.0"}, follow_redirects=False)
    assert r.status_code == 403


def test_basic_auth_needs_no_csrf(client):
    import base64
    hdr = {"authorization": "Basic " + base64.b64encode(b"finn:geheim12345").decode(), "user-agent": "okhttp/4"}
    r = client.post("/api/me/settings", json={"speed_warn": False}, headers=hdr)
    assert r.status_code == 200


def test_body_limit_on_public_form(client):
    r = client.post("/login", content=b"x" * (200 * 1024), headers={**ORIGIN, "content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


def test_body_limit_without_content_length(client):
    def gen():
        for _ in range(100):
            yield b"x" * 4096
    r = client.post("/login", content=gen(), headers={**ORIGIN, "content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


def test_logout_revokes_copied_cookie(client):
    _login(client)
    stolen = client.cookies.get("ra_session")
    assert client.get("/api/me", headers=ORIGIN).status_code == 200
    client.post("/logout", headers=ORIGIN, follow_redirects=False)
    client.cookies.set("ra_session", stolen, domain="riganalyzer.petrick.top")
    assert client.get("/api/me", headers=ORIGIN).status_code == 401


def test_logout_all_kills_every_session(client):
    from fastapi.testclient import TestClient
    import main
    other = TestClient(main.app, base_url=HOST)
    _login(other)
    h = _login(client)
    assert other.get("/api/me", headers=ORIGIN).status_code == 200
    assert client.post("/api/me/logout-all", json={}, headers=h).status_code == 200
    assert other.get("/api/me", headers=ORIGIN).status_code == 401
    _login(other)   # neu anmelden geht
    assert other.get("/api/me", headers=ORIGIN).status_code == 200


def test_old_cookie_format_still_valid(client):
    import base64
    import time
    exp = int(time.time()) + 3600
    sig = auth._sig("finn", "rig", str(exp))
    old = base64.urlsafe_b64encode(f"finn|rig|{exp}|{sig}".encode()).decode()
    assert auth.read_cookie(old, "rig") == "finn"


def test_users_file_private(client, data_dir):
    auth.save_users(auth.read_disk())
    assert stat.S_IMODE(os.stat(data_dir / "users.json").st_mode) == 0o600


def test_unknown_user_burns_bcrypt(monkeypatch):
    calls = []
    real = bcrypt.checkpw
    monkeypatch.setattr(bcrypt, "checkpw", lambda pw, h: calls.append(1) or real(pw, h))
    assert auth.check("gibtsnicht-xyz", "egal", "rig") is False
    assert calls, "unbekannter Login muss genauso bcrypt rechnen"
