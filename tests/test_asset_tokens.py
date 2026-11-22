"""0.37.3: Jede eigene Skript-/CSS-Datei auf den Startseiten bekommt die Versionsmarke ?v= (Cache-Busting).

Fehlt sie (wie bei autobahn.js zuerst), kann ein Browser eine alte Datei mit einer neuen planner.js kombinieren."""
import json
import re

import pytest

import auth

RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"


@pytest.fixture
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    (data_dir / "users.json").write_text(json.dumps({"users": {"olaf": {"hash": auth.hash_pw("geheim12345"), "apps": ["ride", "rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    import main
    return lambda host: TestClient(main.app, base_url=host)


@pytest.mark.parametrize("host", [RIDE, RIG])
def test_alle_eigenen_assets_mit_versionsmarke(client, host):
    c = client(host)
    o = {"origin": host, "user-agent": "Mozilla/5.0"}
    c.post("/login", data={"user": "olaf", "password": "geheim12345"}, headers=o, follow_redirects=False)
    html = c.get("/", headers=o).text
    refs = re.findall(r'(?:src|href)="(/static(?:-rig)?/[^"]+\.(?:js|css))(\?v=[0-9a-f]+)?"', html)
    eigen = [(u, v) for u, v in refs if "/vendor/" not in u]
    assert eigen, "keine Assets gefunden"
    ohne = [u for u, v in eigen if not v]
    assert not ohne, f"ohne ?v=: {ohne}"
