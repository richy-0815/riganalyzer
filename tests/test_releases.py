import json
import os

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import auth

HOST = "https://riganalyzer.petrick.top"
ORIGIN = {"origin": HOST, "user-agent": "Mozilla/5.0"}
REL = json.load(open(os.path.join(os.path.dirname(__file__), "..", "app", "releases.json"), encoding="utf-8"))


@pytest.fixture
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True}, "olaf": {"hash": h, "apps": ["rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    (data_dir / "app").mkdir()
    (data_dir / "app" / "rignavi.json").write_text('{"version": "0.9.5", "code": 8}')
    import main
    return TestClient(main.app, base_url=HOST)


RIG_WORDS = ("Wohnmobil", "Womo", "Gespann", "RigNavi", "RigAnalyzer", "Navi-Paket", "Stellpl", "Etappe", "Tankplan", "Anhänger",
             "Umweltzone")
RIDE_WORDS = ("Motorrad", "Naked Bike", "Wheelie", "RideAnalyzer")


def _text(r, app):
    return r.get(f"public_text_{app}") or r["public_text"]


def test_releases_json_has_public_short_entries():
    """Seit 0.41.1 (#34) alle oeffentlich vertretbaren Eintraege – kurz, je App ohne Begriffe der anderen App."""
    pub = [r for r in REL if r.get("public")]
    assert len(pub) >= 4
    for r in pub:
        assert r.get("public_text"), r["version"]
        for app in ("ride", "rig"):
            if r["app"] not in (app, "beide"):
                continue
            t = _text(r, app) + " " + (r.get(f"title_{app}") or r["title"])
            assert len(_text(r, app)) <= 220, (r["version"], app)
            bad = [w for w in (RIG_WORDS if app == "ride" else RIDE_WORDS) if w in t]
            assert not bad, f"{r['version']} ({app}-Seite): {bad}"
            assert " du " not in f" {t} " and " dein" not in t, f"{r['version']}: ihr-Form"


def test_public_page_shows_only_public_short_texts(client):
    client.post("/login", data={"user": "olaf", "password": "geheim12345"}, headers=ORIGIN, follow_redirects=False)
    page = client.get("/releases.html", headers=ORIGIN).text
    import html
    for r in REL:
        if r.get("public") and r["app"] in ("rig", "beide"):   # Rig-Seite: Ride-Eintraege stehen dort nicht
            assert html.escape(_text(r, "rig")) in page, r["version"]
        else:
            assert f'id="v{r["version"]}"' not in page
    assert "Zweiter Faktor" not in page and "2FA" not in page


def test_admin_versions(client):
    from conftest import enable_admin_2fa
    client.post("/login", data={"user": "bofh", "password": "geheim12345"}, headers=ORIGIN, follow_redirects=False)
    enable_admin_2fa(client, ORIGIN)
    v = client.get("/api/admin/versions", headers=ORIGIN).json()
    assert v["running"]["server"] and v["running"]["app"] == "0.9.5"
    # nur die Eintraege der App dieser Seite (Test-Host ist der RigAnalyzer)
    # Eintraege nur mit "intern"-Punkten erscheinen nirgends
    shown = [r for r in REL if not r.get("pending") and r["app"] in ("rig", "beide")
             and any(len(it) < 3 or it[2] in ("rig", "both") for it in r["items"])]
    assert [r["version"] for r in v["releases"]] == [r["version"] for r in shown]
    assert all(it[2:] != ["intern"] for r in v["releases"] for it in r["items"])


def test_admin_versions_needs_admin(client):
    client.post("/login", data={"user": "olaf", "password": "geheim12345"}, headers=ORIGIN, follow_redirects=False)
    assert client.get("/api/admin/versions", headers=ORIGIN).status_code == 404


def test_version_matches_newest_release():
    """Jede Aenderung bekommt eine eigene Versionsnummer (seit 0.25.2): VERSION in main.py = neuester Eintrag."""
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "main.py"), encoding="utf-8").read()
    version = re.search(r'^VERSION = "([^"]+)"', src, re.M).group(1)
    newest = next(r for r in REL if not r.get("pending"))
    assert version == newest["version"], f"main.py {version} ≠ releases.json {newest['version']}"


def test_public_page_blocks_of_five(client):
    """Die 5 neuesten offen, danach je 5 hinter „Mehr anzeigen“ (verschachtelt)."""
    import re
    client.post("/login", data={"user": "olaf", "password": "geheim12345"}, headers=ORIGIN, follow_redirects=False)
    page = client.get("/releases.html", headers=ORIGIN).text
    n = sum(1 for r in REL if r.get("public") and r["app"] in ("rig", "beide"))
    first = page.split('<details class="rel-more">')[0]
    assert first.count('<article class="rel') == min(5, n)
    assert page.count('<details class="rel-more">') == max(0, (n - 1) // 5)
    assert re.search(r"Mehr anzeigen \(5 von \d+ älteren\)", page)
