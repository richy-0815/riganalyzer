"""0.36.0: „Meine Touren“ im Konto (nur RigAnalyzer), „neu“-Abzeichen an „Route planen“ weg."""
import json
import os

import pytest

import auth

RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"
APP = os.path.join(os.path.dirname(__file__), "..", "app")


@pytest.fixture
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {"olaf": {"hash": h, "apps": ["ride", "rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    import main

    def make(host):
        c = TestClient(main.app, base_url=host)
        o = {"origin": host, "user-agent": "Mozilla/5.0"}
        c.post("/login", data={"user": "olaf", "password": "geheim12345"}, headers=o, follow_redirects=False)
        return c, o
    return make


def test_rig_konto_hat_meine_touren(client):
    c, o = client(RIG)
    page = c.get("/konto", headers=o).text
    assert 'id="kt-touren"' in page and "konto-touren.js" in page and "Meine Touren" in page


def test_ride_konto_ohne_touren(client):
    c, o = client(RIDE)
    page = c.get("/konto", headers=o).text
    assert "kt-touren" not in page and "konto-touren.js" not in page and "Meine Touren" not in page


@pytest.mark.parametrize("folder", ["static-ride", "static-rig"])
def test_route_planen_ohne_neu_abzeichen(folder):
    with open(os.path.join(APP, folder, "index.html"), encoding="utf-8") as f:
        html = f.read()
    assert 'class="badge new"' not in html


def test_rig_konto_ohne_navi_einstellung_aber_mit_hinweis(client):
    """0.36.0: Tempo-Warnung wirkt nur in RigNavi und steht nur noch dort; das Konto verweist auf die App."""
    c, o = client(RIG)
    page = c.get("/konto", headers=o).text
    assert "kt-speedwarn" not in page and 'id="kt-rignavi"' in page and 'href="/app.html"' in page


def test_ride_konto_ohne_rignavi(client):
    c, o = client(RIDE)
    assert "RigNavi" not in c.get("/konto", headers=o).text


def test_rignavi_anleitung_sicherheitshinweis(data_dir):
    import main
    page = main._app_html(True)
    assert 'class="sec-hint"' in page and "wieder ausschalten" in page and "nur von <b>dieser Seite</b>" in page
