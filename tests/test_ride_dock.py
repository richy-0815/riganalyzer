"""0.37.5: RideAnalyzer-Hauptseite mit verschiebbaren Ergebnis-Karten (Dock wie Rig, gemeinsames dock.js).

Anordnung am Login unter prefs „ride_ui“ – eigener Bereich, denn „ui“ gehoert Rig und die Bereiche gelten je Login
fuer beide Apps."""
import json
import os
import re

import pytest

import auth
import prefs
from test_api_trips import basic, client  # noqa: F401  (Fixture client)

APP = os.path.join(os.path.dirname(__file__), "..", "app")
RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"


def _html(folder):
    with open(os.path.join(APP, folder, "index.html"), encoding="utf-8") as f:
        return f.read()


def test_ride_karten_haben_data_card_und_docks():
    html = _html("static-ride")
    ids = re.findall(r'data-card="([a-z]+)"', html)
    assert sorted(ids) == sorted(prefs.RIDE_CARDS)
    assert len(ids) == len(set(ids))
    for i in ids:   # jede Karte hat einen Titel (eingeklappt sichtbar)
        assert re.search(rf'data-card="{i}"[^>]*data-title="[^"]+"', html), i
    assert 'id="dock-left"' in html and 'id="dock-right"' in html and 'id="dock-reset"' in html
    # Ergebnis-Karten erst nach einer Analyse
    for i in ("result", "profiles", "highlights", "weather", "list", "quality", "compare", "band"):
        assert re.search(rf'data-card="{i}"[^>]*data-result="1"', html), i
    for i in ("upload", "planner"):
        assert not re.search(rf'data-card="{i}"[^>]*data-result', html), i


def test_beide_seiten_laden_dock_js_und_dock_css():
    for folder in ("static-ride", "static-rig"):
        html = _html(folder)
        assert '<script src="/static/dock.js"></script>' in html, folder
        assert '<link rel="stylesheet" href="/static/dock.css">' in html, folder
        # vor dem App-Skript, das DockKit.mount aufruft
        app = "/static-ride/app.js" if folder == "static-ride" else "/static-rig/rig.js"
        assert html.index("/static/dock.js") < html.index(app), folder


def test_dock_css_nur_noch_an_einer_stelle():
    with open(os.path.join(APP, "static-rig", "rig.css"), encoding="utf-8") as f:
        assert ".dock-tools {" not in f.read()
    with open(os.path.join(APP, "static", "dock.css"), encoding="utf-8") as f:
        assert ".dock-tools {" in f.read()


@pytest.fixture
def web(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    (data_dir / "users.json").write_text(json.dumps({"users": {"olaf": {"hash": auth.hash_pw("geheim12345"), "apps": ["ride", "rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    import main

    def get(host):
        c = TestClient(main.app, base_url=host)
        o = {"origin": host, "user-agent": "Mozilla/5.0"}
        c.post("/login", data={"user": "olaf", "password": "geheim12345"}, headers=o, follow_redirects=False)
        return c.get("/", headers=o).text
    return get


@pytest.mark.parametrize("host", [RIDE, RIG])
def test_dock_assets_mit_versionsmarke(web, host):
    html = web(host)
    assert re.search(r'src="/static/dock\.js\?v=[0-9a-f]+"', html)
    assert re.search(r'href="/static/dock\.css\?v=[0-9a-f]+"', html)


LAY = {"left": ["upload", "planner", "result", "profiles", "highlights", "weather", "list", "quality"],
       "right": ["band", "compare"], "collapsed": ["quality"]}


def test_prefs_ride_ui_roundtrip_und_neuer_gewinnt(client):  # noqa: F811 – Fixture (Import oben)
    r = client.put("/api/me/prefs", json={"ride_ui": {**LAY, "at": 2000}}, headers=basic("richy"))
    assert r.status_code == 200, r.text
    client.put("/api/me/prefs", json={"ride_ui": {"left": [], "right": [], "collapsed": [], "at": 1000}}, headers=basic("richy"))
    assert client.get("/api/me/prefs", headers=basic("richy")).json()["ride_ui"]["right"] == ["band", "compare"]


def test_prefs_ride_ui_und_rig_ui_stoeren_sich_nicht(client):  # noqa: F811 – Fixture (Import oben)
    rig = {"tab": "steep", "layout": {"left": ["trip"], "right": [], "collapsed": []}, "at": 3000}
    assert client.put("/api/me/prefs", json={"ui": rig}, headers=basic("richy")).status_code == 200
    assert client.put("/api/me/prefs", json={"ride_ui": {**LAY, "at": 4000}}, headers=basic("richy")).status_code == 200
    d = client.get("/api/me/prefs", headers=basic("richy")).json()
    assert d["ui"]["layout"]["left"] == ["trip"] and d["ride_ui"]["left"][0] == "upload"


@pytest.mark.parametrize("bad", [
    {"left": ["boese"]},                                   # unbekannte Karte
    {"left": ["trip"]},                                    # Rig-Karte gibt es in Ride nicht
    {"left": ["upload", "upload"]},                        # doppelt
    {"left": ["upload"], "right": ["upload"]},             # auf beiden Seiten
    {"collapsed": ["band", "band"]},
    {"left": "upload"},                                    # keine Liste
    {"left": [1]},
    {"tab": "notes"},                                      # unbekannter Eintrag
    {"right": ["band"] * 20},
])
def test_prefs_ride_ui_streng(client, bad):  # noqa: F811 – Fixture (Import oben)
    r = client.put("/api/me/prefs", json={"ride_ui": {**bad, "at": 1}}, headers=basic("richy"))
    assert r.status_code == 422, bad


def test_prefs_ride_ui_ohne_zeitstempel_abgelehnt(client):  # noqa: F811 – Fixture (Import oben)
    assert client.put("/api/me/prefs", json={"ride_ui": LAY}, headers=basic("richy")).status_code == 422
