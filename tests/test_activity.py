import base64
import json
import os
import time

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import activity
import auth

HOST = "https://riganalyzer.petrick.top"
ORIGIN = {"origin": HOST, "user-agent": "Mozilla/5.0"}


@pytest.fixture(autouse=True)
def fresh(data_dir, monkeypatch):
    activity.reset_for_tests()
    monkeypatch.setattr(activity, "rdns", lambda ip: "heimnetz" if ip.startswith("10.0.0.") else "")


def test_action_for_paths():
    assert activity.action_for("POST", "/api/plan") == "plant eine Route"
    assert activity.action_for("POST", "/api/analyze") == "prüft eine GPX"
    assert activity.action_for("POST", "/api/trips") == "schickt eine Reise ans Navi"
    assert activity.action_for("GET", "/api/trips/abc/package/strassen.tar") == "lädt ein Reisepaket"
    assert activity.action_for("POST", "/api/nav/route") == "Navi berechnet Route"
    assert activity.action_for("GET", "/static/style.css") is None


def test_login_history_capped_and_aged(monkeypatch):
    for i in range(25):
        activity.record_login("olaf", f"10.0.0.{i}", "Browser", True)
    activity.record_login("olaf", "6.6.6.6", "Browser", False)
    h = activity.logins("olaf")
    assert len(h) == 20 and h[0]["ip"] == "6.6.6.6" and h[0]["ok"] is False
    real = time.time
    monkeypatch.setattr(activity.time, "time", lambda: real() + 31 * 86400)
    assert activity.logins("olaf") == []


def test_online_state_and_action(monkeypatch):
    activity.touch("finn", "10.0.0.20", "Browser", "POST", "/api/plan")
    s = {u["name"]: u for u in activity.snapshot(["finn", "olaf"])}
    assert s["finn"]["state"] == "online" and s["finn"]["action"] == "plant eine Route"
    assert s["finn"]["ip"] == "10.0.0.20" and s["finn"]["host"] == "heimnetz"
    assert s["olaf"]["state"] == "nie"
    real = time.time
    monkeypatch.setattr(activity.time, "time", lambda: real() + 600)
    assert {u["name"]: u for u in activity.snapshot(["finn"])}["finn"]["state"] == "heute"


def test_heartbeat_rounds_position_and_respects_consent():
    activity.heartbeat("olaf", "1.2.3.4", {"dest": "Moustiers", "remaining_m": 38000, "eta_s": 2400, "source": "package",
                                          "lat": 43.84731, "lon": 6.22129})
    n = {u["name"]: u for u in activity.snapshot(["olaf"])}["olaf"]["navi"]
    assert n["dest"] == "Moustiers" and n["lat"] == 43.85 and n["lon"] == 6.22 and n["source"] == "package"
    activity.heartbeat("olaf", "1.2.3.4", {"dest": "Riez", "remaining_m": 10000, "eta_s": 600})
    n = {u["name"]: u for u in activity.snapshot(["olaf"])}["olaf"]["navi"]
    assert "lat" not in n and n["dest"] == "Riez"


def test_position_dropped_after_24h(monkeypatch):
    activity.heartbeat("olaf", "1.2.3.4", {"dest": "X", "lat": 43.8, "lon": 6.2})
    real = time.time
    monkeypatch.setattr(activity.time, "time", lambda: real() + 25 * 3600)
    assert activity.snapshot(["olaf"])[0]["navi"] is None


def test_navi_end_clears():
    activity.heartbeat("olaf", "1.2.3.4", {"dest": "X"})
    activity.heartbeat("olaf", "1.2.3.4", {"ended": True})
    assert activity.snapshot(["olaf"])[0]["navi"] is None


def test_persist_and_load(data_dir):
    activity.record_login("olaf", "1.1.1.1", "RigNavi", True)
    activity.touch("olaf", "1.1.1.1", "RigNavi", "POST", "/api/nav/route")
    activity.save()
    activity.reset_for_tests(load=True)
    assert activity.logins("olaf")[0]["ip"] == "1.1.1.1"
    assert activity.snapshot(["olaf"])[0]["action"] == "Navi berechnet Route"


# ---------------------------------------------------------------- HTTP
@pytest.fixture
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True},
        "olaf": {"hash": h, "apps": ["rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    import main
    return TestClient(main.app, base_url=HOST)


def basic(user):
    return {"user-agent": "okhttp/5", "authorization": "Basic " + base64.b64encode(f"{user}:geheim12345".encode()).decode()}


def test_ping_from_app_and_admin_view(client):
    from conftest import enable_admin_2fa
    assert client.post("/api/nav/ping", json={"dest": "Moustiers", "lat": 43.8, "lon": 6.2}, headers=basic("olaf")).status_code == 204
    client.post("/login", data={"user": "olaf", "password": "falsch1234"}, headers=ORIGIN)
    client.post("/login", data={"user": "bofh", "password": "geheim12345"}, headers=ORIGIN, follow_redirects=False)
    enable_admin_2fa(client, ORIGIN)
    r = client.get("/api/admin/activity", headers=ORIGIN).json()
    olaf = next(u for u in r["users"] if u["name"] == "olaf")
    assert olaf["navi"]["dest"] == "Moustiers" and olaf["device"] == "RigNavi" and olaf["state"] == "online"
    assert any(not entry["ok"] for entry in r["logins"]["olaf"])           # Fehlversuch protokolliert


def test_activity_needs_admin(client):
    client.post("/login", data={"user": "olaf", "password": "geheim12345"}, headers=ORIGIN, follow_redirects=False)
    assert client.get("/api/admin/activity", headers=ORIGIN).status_code == 404


def test_heartbeat_idle_app_offen_ohne_ziel():
    """0.36.1: RigNavi meldet bei Zustimmung die Position auch ohne Navigation (App offen, alle 5 min)."""
    activity.heartbeat("olaf", "1.2.3.4", {"idle": True, "lat": 52.51234, "lon": 13.38765})
    n = activity.snapshot(["olaf"])[0]["navi"]
    assert n["idle"] is True and n["lat"] == 52.51 and n["lon"] == 13.39 and n["dest"] == ""
    # Navigation startet: kein idle mehr
    activity.heartbeat("olaf", "1.2.3.4", {"dest": "Riez", "lat": 43.8, "lon": 6.1})
    n = activity.snapshot(["olaf"])[0]["navi"]
    assert "idle" not in n and n["dest"] == "Riez"


def test_heartbeat_idle_ohne_position_wird_ignoriert():
    activity.heartbeat("olaf", "1.2.3.4", {"dest": "Riez"})
    activity.heartbeat("olaf", "1.2.3.4", {"idle": True})   # ohne Zustimmung schickt die App nichts – falls doch: Fahrt bleibt
    assert activity.snapshot(["olaf"])[0]["navi"]["dest"] == "Riez"
