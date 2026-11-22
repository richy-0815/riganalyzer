"""0.45.0 (#3): Touren teilen – API, Mail, Protokoll, Login löschen."""
import json

import pytest

import auth
import shares
import trips

RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"
STOPS = [{"lat": 52.5, "lon": 13.4, "name": "Berlin"}, {"lat": 51.7, "lon": 14.3, "name": "Cottbus"}]


@pytest.fixture
def env(data_dir, monkeypatch):
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True},
        "olaf": {"hash": h, "apps": ["rig"], "mail": "olaf@example.org"},
        "otto": {"hash": h, "apps": ["rig"], "mail": "otto@example.org"},
        "rita": {"hash": h, "apps": ["ride"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    import main  # noqa: F401 – App samt Rig-Router laden
    import rig_routes
    sent = []
    monkeypatch.setattr(rig_routes, "_share_mail", lambda to, subj, body: sent.append((to, subj, body)))
    monkeypatch.setattr(rig_routes.package, "request", lambda user, tid: None)
    return sent


def client(host, user):
    from fastapi.testclient import TestClient
    import main
    o = {"origin": host, "user-agent": "Mozilla/5.0"}
    c = TestClient(main.app, base_url=host)
    c.post("/login", data={"user": user, "password": "geheim12345"}, headers=o, follow_redirects=False)
    return c, {**o, "x-ra-csrf": c.cookies.get("ra_csrf")}


def _tour(name="Lausitz"):
    return trips.save("olaf", {"name": name, "stops": STOPS, "note": "privat"})


def test_api_teilen_annehmen(env):
    t = _tour()
    c, h = client(RIG, "olaf")
    r = c.post("/api/shares", json={"trip_id": t["id"], "to": "otto"}, headers=h)
    assert r.status_code == 200 and r.json() == {"ok": True, "text": shares.SENT_TEXT}
    o, oh = client(RIG, "otto")
    box = o.get("/api/shares", headers=oh).json()
    assert len(box["inbox"]) == 1 and box["outbox"] == []
    r = o.post(f"/api/shares/{box['inbox'][0]['id']}/accept", headers=oh)
    assert r.status_code == 200 and r.json()["trip"]["name"] == "Lausitz"
    assert [x["name"] for x in o.get("/api/trips", headers=oh).json()] == ["Lausitz"]
    assert c.get("/api/shares", headers=h).json()["outbox"][0]["state"] == "angenommen"


def test_api_antwort_gleich(env):
    t = _tour()
    c, h = client(RIG, "olaf")
    got = []
    for to in ("otto", "niemand", "rita", "olaf"):
        r = c.post("/api/shares", json={"trip_id": t["id"], "to": to}, headers=h)
        got.append((r.status_code, r.json()))
    assert len(set(json.dumps(x) for x in got)) == 1


def test_api_ride_404(env):
    t = _tour()
    c, h = client(RIDE, "rita")
    assert c.get("/api/shares", headers=h).status_code == 404
    assert c.post("/api/shares", json={"trip_id": t["id"], "to": "otto"}, headers=h).status_code == 404
    assert c.post("/api/shares/" + "0" * 16 + "/accept", headers=h).status_code == 404


def test_api_ohne_csrf_403(env):
    t = _tour()
    c, h = client(RIG, "olaf")
    ohne = {k: v for k, v in h.items() if k != "x-ra-csrf"}
    assert c.post("/api/shares", json={"trip_id": t["id"], "to": "otto"}, headers=ohne).status_code == 403
    assert c.delete("/api/shares/" + "0" * 16, headers=ohne).status_code == 403
    assert shares.outbox("olaf") == []


def test_api_fremd_und_kaputt(env):
    t = _tour()
    o, oh = client(RIG, "otto")
    assert o.post("/api/shares", json={"trip_id": t["id"], "to": "olaf"}, headers=oh).status_code == 404
    assert o.post("/api/shares", json={"trip_id": t["id"]}, headers=oh).status_code == 422
    assert o.post("/api/shares", content=b"kein json", headers={**oh, "content-type": "application/json"}).status_code == 400
    assert o.delete("/api/shares/" + "0" * 16, headers=oh).status_code == 404


def test_api_bremse_429(env):
    t = _tour()
    c, h = client(RIG, "olaf")
    for i in range(shares.PER_DAY):
        c.post("/api/shares", json={"trip_id": t["id"], "to": f"x{i}"}, headers=h)
    r = c.post("/api/shares", json={"trip_id": t["id"], "to": "x99"}, headers=h)
    assert r.status_code == 429 and "morgen" in r.json()["detail"]


def test_api_mail_nur_zustellbar(env):
    t = trips.save("olaf", {"name": "A\nB\x07", "stops": STOPS})
    c, h = client(RIG, "olaf")
    c.post("/api/shares", json={"trip_id": t["id"], "to": "niemand"}, headers=h)
    assert env == []
    c.post("/api/shares", json={"trip_id": t["id"], "to": "otto"}, headers=h)
    (to, subj, body), = env
    assert to == "otto@example.org" and "\n" not in subj and "\x07" not in subj and "olaf" in subj
    assert "http" not in body


def test_api_protokoll(env, caplog):
    t = _tour()
    c, h = client(RIG, "olaf")
    o, oh = client(RIG, "otto")
    with caplog.at_level("INFO", logger="zugriff"):
        c.post("/api/shares", json={"trip_id": t["id"], "to": "otto"}, headers=h)
        sid = o.get("/api/shares", headers=oh).json()["inbox"][0]["id"]
        o.post(f"/api/shares/{sid}/accept", headers=oh)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("teilt Tour" in m and "otto" in m for m in msgs)
    assert any("nimmt geteilte Tour" in m for m in msgs)


def test_api_ablehnen_und_zurueckziehen(env):
    t = _tour()
    c, h = client(RIG, "olaf")
    o, oh = client(RIG, "otto")
    c.post("/api/shares", json={"trip_id": t["id"], "to": "otto"}, headers=h)
    sid = o.get("/api/shares", headers=oh).json()["inbox"][0]["id"]
    assert c.delete(f"/api/shares/{sid}", headers=h).status_code == 200
    assert o.get("/api/shares", headers=oh).json()["inbox"] == []
    t2 = _tour("Zwei")
    c.post("/api/shares", json={"trip_id": t2["id"], "to": "otto"}, headers=h)
    sid = o.get("/api/shares", headers=oh).json()["inbox"][0]["id"]
    assert o.post(f"/api/shares/{sid}/decline", headers=oh).status_code == 200
    assert o.get("/api/trips", headers=oh).json() == []


def test_admin_loeschen_raeumt_auf(env):
    t = _tour()
    shares.invite("olaf", "otto", t["id"])
    from test_admin_split import admin_on
    a, ah, _ = admin_on(RIG)
    assert a.delete("/api/admin/users/otto", headers=ah).status_code == 204
    assert shares.outbox("olaf") == []


def test_api_wiederholen_nur_eine_mail(env):
    t = _tour()
    c, h = client(RIG, "olaf")
    for _ in range(3):
        assert c.post("/api/shares", json={"trip_id": t["id"], "to": "otto"}, headers=h).status_code == 200
    assert len(env) == 1


def test_api_basic_auth_404(env):
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app, base_url=RIG)
    r = c.get("/api/shares", auth=("olaf", "geheim12345"), headers={"user-agent": "okhttp/4.12.0"})
    assert r.status_code == 404


def test_geteilt_zeile_nicht_als_paketzeile():
    # „geteilt mit …“ trug die Klasse kt-pkg – der Konto-Test fand zwei Paketzeilen je Tour
    import os
    import re
    src = open(os.path.join(os.path.dirname(shares.__file__), "static-rig", "konto-touren.js"), encoding="utf-8").read()
    classes = re.findall(r'className: "([^"]*kt-shared[^"]*)"', src)
    assert classes and all("kt-pkg" not in c.split() for c in classes)
