"""Analyse abbrechen (0.28.0): Flag am Job, Lauf faellt beim naechsten Fortschritt heraus; nur eigene Jobs."""
import time
import uuid

import analyze
from test_admin import basic, env  # noqa: F401  (Fixture env)


def _job(main, by="olaf@1.2.3.4", state="running"):
    jid = uuid.uuid4().hex   # nicht time_ns(): auf dem Mac zweimal hintereinander derselbe Wert
    main.jobs[jid] = {"state": state, "progress": 10, "msg": "x", "t": time.time(), "by": by}
    return jid


def test_cancel_before_start(env):  # noqa: F811 – Fixture (Import oben)
    import main
    jid = _job(main, state="queued")
    main.jobs[jid]["cancel"] = True
    main._run(jid, b"<gpx/>", "a.gpx", {})
    assert main.jobs[jid]["state"] == "cancelled"


def test_cancel_while_running(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    import main
    jid = _job(main)

    def fake(raw, name, progress, info, options):
        progress(10, "los")
        main.jobs[jid]["cancel"] = True
        progress(50, "weiter")          # hier faellt der Lauf heraus
        raise AssertionError("haette abbrechen muessen")
    monkeypatch.setattr(analyze, "analyze", fake)
    main._run(jid, b"<gpx/>", "a.gpx", {})
    assert main.jobs[jid]["state"] == "cancelled"


def test_api_only_own_jobs(env):  # noqa: F811 – Fixture (Import oben)
    import main
    rig = "https://riganalyzer.petrick.top"
    own = _job(main, by="olaf@1.2.3.4")
    other = _job(main, by="jemand@1.2.3.4")
    from fastapi.testclient import TestClient
    c = TestClient(main.app, base_url=rig)
    assert c.post(f"/api/jobs/{other}/cancel", headers=basic("olaf")).status_code == 403
    r = c.post(f"/api/jobs/{own}/cancel", headers=basic("olaf"))
    assert r.status_code == 200 and r.json()["cancel"] is True and main.jobs[own]["cancel"] is True
    # fremde Analysen nur der Oberadmin per Cookie mit 2FA – per Basic-Auth nicht mehr
    assert c.post(f"/api/jobs/{other}/cancel", headers=basic("bofh")).status_code == 403
    from conftest import enable_admin_2fa
    origin = {"origin": rig, "user-agent": "Mozilla/5.0"}
    a = TestClient(main.app, base_url=rig)
    a.post("/login", data={"user": "bofh", "password": "geheim12345"}, headers=origin, follow_redirects=False)
    enable_admin_2fa(a, origin)
    h = {**origin, "x-ra-csrf": a.cookies.get("ra_csrf")}
    assert a.post(f"/api/jobs/{other}/cancel", headers=h).status_code == 200   # Oberadmin mit 2FA darf
    assert c.post("/api/jobs/gibtsnicht/cancel", headers=basic("olaf")).status_code == 404


def test_status_only_own_jobs(env):  # noqa: F811 – Fixture (Import oben)
    """0.51.2 (Sicherheitscheck 20.11.2026): Status/Ergebnis nur fuer den eigenen Job; fremde wie nicht vorhanden (404)."""
    import main
    rig = "https://riganalyzer.petrick.top"
    own = _job(main, by="olaf@1.2.3.4", state="done")
    other = _job(main, by="jemand@1.2.3.4", state="done")
    main.jobs[other]["result"] = {"geheim": True}
    from fastapi.testclient import TestClient
    c = TestClient(main.app, base_url=rig)
    assert c.get(f"/api/jobs/{own}", headers=basic("olaf")).status_code == 200
    r = c.get(f"/api/jobs/{other}", headers=basic("olaf"))
    assert r.status_code == 404 and "geheim" not in r.text
    assert c.get("/api/jobs/gibtsnicht", headers=basic("olaf")).status_code == 404
    # Oberadmin per Cookie mit 2FA sieht fremde Analysen wie beim Abbrechen
    from conftest import enable_admin_2fa
    origin = {"origin": rig, "user-agent": "Mozilla/5.0"}
    a = TestClient(main.app, base_url=rig)
    a.post("/login", data={"user": "bofh", "password": "geheim12345"}, headers=origin, follow_redirects=False)
    enable_admin_2fa(a, origin)
    assert a.get(f"/api/jobs/{other}", headers=origin).status_code == 200
