"""0.34.0: Kostenuebersicht-API – nur Oberadmin auf dem Rig-Host, sonst 404."""
import json

import pyotp
import pytest

import auth

RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"


@pytest.fixture
def env(data_dir, monkeypatch):
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True},
        "otto": {"hash": h, "apps": ["ride"]},
        "olaf": {"hash": h, "apps": ["rig"]},
        "mia": {"hash": h, "apps": ["ride", "rig"], "admin": {"apps": ["ride", "rig"], "groups": ["g1"]}, "group": "g1"}}}))
    (data_dir / "groups.json").write_text(json.dumps({"groups": {"g1": {"name": "XJR-Club"}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    return data_dir


def admin_on(host, secret=None):
    """bofh auf host anmelden; 2FA beim ersten Mal einrichten, sonst Code eingeben -> (client, headers, secret)."""
    from fastapi.testclient import TestClient
    from conftest import enable_admin_2fa
    import main
    o = {"origin": host, "user-agent": "Mozilla/5.0"}
    c = TestClient(main.app, base_url=host)
    c.post("/login", data={"user": "bofh", "password": "geheim12345"}, headers=o, follow_redirects=False)
    if secret:   # derselbe Code gilt nur einmal – den naechsten nehmen
        import time
        c.post("/zweiter-faktor", data={"code": pyotp.TOTP(secret).at(time.time() + 30)}, headers=o, follow_redirects=False)
    else:
        secret = enable_admin_2fa(c, o)
    return c, {**o, "x-ra-csrf": c.cookies.get("ra_csrf")}, secret


def login_as(host, user):
    from fastapi.testclient import TestClient
    from conftest import enable_admin_2fa
    import main
    o = {"origin": host, "user-agent": "Mozilla/5.0"}
    c = TestClient(main.app, base_url=host)
    r = c.post("/login", data={"user": user, "password": "geheim12345"}, headers=o, follow_redirects=False)
    assert r.status_code == 303, r.text
    if user == "mia":
        enable_admin_2fa(c, o)
    return c, {**o, "x-ra-csrf": c.cookies.get("ra_csrf")}


def test_costs_oberadmin_rig(env):
    import datetime
    import usage
    usage.add("olaf", "rig", analyses=1)
    usage.add("otto", "ride", analyses=1)
    c, h, _ = admin_on(RIG)
    r = c.get("/api/admin/costs", headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j["scenarios"]) == {"home", "home_s3", "cloud"}
    sc = j["scenarios"]["home"]
    assert "otto" not in [a["name"] for a in sc["accounts"]]
    assert "olaf" in [a["name"] for a in sc["accounts"]]
    assert j["month"] == datetime.date.today().strftime("%Y-%m") and j["month"] in j["months"]
    assert "abo_eur" in j["prices"] and "since" in j


def test_costs_404_others(env):
    c, h, _ = admin_on(RIDE)
    assert c.get("/api/admin/costs", headers=h).status_code == 404
    assert c.put("/api/admin/costs/prices", json={"abo_eur": 5}, headers=h).status_code == 404
    m, mh = login_as(RIG, "mia")
    assert m.get("/api/admin/costs", headers=mh).status_code == 404
    assert m.put("/api/admin/costs/prices", json={"abo_eur": 5}, headers=mh).status_code == 404
    u, uh = login_as(RIG, "olaf")
    assert u.get("/api/admin/costs", headers=uh).status_code == 404
    assert u.put("/api/admin/costs/prices", json={"abo_eur": 5}, headers=uh).status_code == 404


def test_costs_month_validation(env):
    c, h, _ = admin_on(RIG)
    for bad in ("2026-13", "abc", "2026-00", "2026-1", "2026-05\n"):
        assert c.get("/api/admin/costs", params={"month": bad}, headers=h).status_code == 422, bad
    r = c.get("/api/admin/costs", params={"month": "2020-01"}, headers=h)
    assert r.status_code == 200 and r.json()["month"] == "2020-01"
    assert set(r.json()["scenarios"]) == {"home", "home_s3", "cloud"}


def test_costs_put_prices(env):
    import admin
    c, h, _ = admin_on(RIG)
    r = c.put("/api/admin/costs/prices", json={"abo_eur": 7.5}, headers=h)
    assert r.status_code == 200 and r.json()["prices"]["abo_eur"] == 7.5
    assert c.get("/api/admin/costs", headers=h).json()["prices"]["abo_eur"] == 7.5
    r = c.put("/api/admin/costs/prices", json={"foo": 1}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]
    h2 = {k: v for k, v in h.items() if k != "x-ra-csrf"}
    assert c.put("/api/admin/costs/prices", json={"abo_eur": 9}, headers=h2).status_code == 403
    assert any("Preise geändert" in str(e) for e in admin.log_for("bofh", "all", "rig"))


def test_admin_page_cost_card_only_on_rig(env):
    c, h, sec = admin_on(RIG)
    assert 'id="cost-card"' in c.get("/admin", headers=h).text
    assert "admin-costs.js" in c.get("/admin", headers=h).text
    c2, h2, _ = admin_on(RIDE, sec)
    assert 'id="cost-card"' not in c2.get("/admin", headers=h2).text
    assert "admin-costs.js" not in c2.get("/admin", headers=h2).text


# ---------------------------------------------------------------- Entwicklung (Claude) je Login
def test_costs_api_ohne_devcosts(env):
    c, h, _ = admin_on(RIG)
    j = c.get("/api/admin/costs", headers=h).json()
    assert j["dev"] is None


def test_costs_api_mit_devcosts(env):
    (env / "devcosts.json").write_text(json.dumps({"stand": "2026-11-09T12:00:00", "tickets": [
        {"nr": 20, "titel": "t", "status": "offen", "feedback_id": "a1b2c3d4e5f6", "login": "olaf", "app": "rig",
         "laeufe": [{"zeit": "2026-11-08T15:00:00", "usd": 1.0, "modell": "claude-sonnet-5-5"}]}]}))
    c, h, _ = admin_on(RIG)
    j = c.get("/api/admin/costs?month=2026-11", headers=h).json()
    assert j["dev"]["logins"][0]["login"] == "olaf"
    assert j["dev"]["logins"][0]["eur"] == pytest.approx(j["prices"]["usd_eur"])


def test_preise_usd_eur_default(env):
    import costs
    assert costs.load_prices()["usd_eur"] == 0.92
    assert costs.save_prices({"usd_eur": 0.9})["usd_eur"] == 0.9
