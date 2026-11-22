"""0.46.0: Warteschlange in main.py – Spur, Wartetext im Status, Zwischenspeicher."""
import json

import pytest

import auth
import resultcache

RIG = "https://riganalyzer.petrick.top"
BERLIN, POTSDAM, MUENCHEN = (52.52, 13.405), (52.39, 13.065), (48.14, 11.58)


def gpx(*pts):
    body = "".join(f'<trkpt lat="{a}" lon="{b}"/>' for a, b in pts)
    return f'<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>{body}</trkseg></trk></gpx>'.encode()


class FakePool:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *a):
        self.calls.append(a)


@pytest.fixture
def env(data_dir, monkeypatch):
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {"olaf": {"hash": h, "apps": ["rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    import main
    pools = {n: FakePool() for n in ("pool", "short_pool", "live_pool")}
    for n, p in pools.items():
        monkeypatch.setattr(main, n, p)
    monkeypatch.setattr(main.analyze, "needs_live", lambda raw, pts=None: False)
    main.jobs.clear()
    return pools


def client():
    from fastapi.testclient import TestClient
    import main
    o = {"origin": RIG, "user-agent": "Mozilla/5.0"}
    c = TestClient(main.app, base_url=RIG)
    c.post("/login", data={"user": "olaf", "password": "geheim12345"}, headers=o, follow_redirects=False)
    return c, {**o, "x-ra-csrf": c.cookies.get("ra_csrf")}


def upload(c, h, raw, name="tour.gpx"):
    r = c.post("/api/analyze", files={"file": (name, raw, "application/gpx+xml")}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["job"]


def test_kurze_route_in_kurzer_spur(env):
    import main
    c, h = client()
    jid = upload(c, h, gpx(BERLIN, POTSDAM))
    assert main.jobs[jid]["lane"] == "kurz" and len(env["short_pool"].calls) == 1 and not env["pool"].calls


def test_lange_route_in_lokaler_spur(env):
    import main
    c, h = client()
    jid = upload(c, h, gpx(BERLIN, MUENCHEN))
    assert main.jobs[jid]["lane"] == "lokal" and len(env["pool"].calls) == 1


def test_wartetext_im_status(env):
    import main
    c, h = client()
    first = upload(c, h, gpx(BERLIN, MUENCHEN))
    main.jobs[first].update(state="running", progress=10)
    second = upload(c, h, gpx(BERLIN, MUENCHEN), "zwei.gpx")
    j = c.get(f"/api/jobs/{second}", headers=h).json()
    assert j["msg"].startswith("Platz 1 in der Warteschlange · noch")
    assert "cache_key" not in j and "seq" not in j


def test_cache_treffer_sofort_fertig(env):
    import main
    raw = gpx(BERLIN, MUENCHEN)
    res = {"stats": {"km": 500}, "file": "alt.gpx"}
    assert resultcache.store(resultcache.key(raw, {"rig": True}, main._cache_stamp()), res)
    c, h = client()
    jid = upload(c, h, raw, "neu.gpx")
    j = c.get(f"/api/jobs/{jid}", headers=h).json()
    assert j["state"] == "done" and j["from_cache"] is True and j["result"]["file"] == "neu.gpx"
    assert not any(p.calls for p in env.values())


def test_run_speichert_und_misst(env, monkeypatch):
    import main
    raw = gpx(BERLIN, MUENCHEN)
    before = main.rate.value()
    monkeypatch.setattr(main.analyze, "analyze", lambda *a, **k: {"stats": {"km": 500, "source": "lokal"}})
    out = main._submit(raw, "t.gpx", {"rig": True}, "olaf@x")
    main._run(out["job"], raw, "t.gpx", {"rig": True})
    assert main.jobs[out["job"]]["state"] == "done"
    assert resultcache.load(resultcache.key(raw, {"rig": True}, main._cache_stamp())) is not None
    assert main.rate.value() != before
    raw2 = gpx(BERLIN, POTSDAM, MUENCHEN)
    monkeypatch.setattr(main.analyze, "analyze", lambda *a, **k: {"stats": {"km": 500}, "missing": [{"region": "x"}]})
    out = main._submit(raw2, "t2.gpx", {"rig": True}, "olaf@x")
    main._run(out["job"], raw2, "t2.gpx", {"rig": True})
    assert resultcache.load(resultcache.key(raw2, {"rig": True}, main._cache_stamp())) is None


def test_beispiel_ohne_ergebnisspeicher(env, monkeypatch):
    import main
    raw = gpx(BERLIN, MUENCHEN)
    monkeypatch.setattr(main.analyze, "analyze", lambda *a, **k: {"stats": {"km": 500}})
    out = main._submit(raw, "verdon.gpx", {"rig": True}, "olaf@x", cache=False)
    main._run(out["job"], raw, "verdon.gpx", {"rig": True})
    assert resultcache.load(resultcache.key(raw, {"rig": True}, main._cache_stamp())) is None


# ---------------------------------------------------------------- Randfaelle
def test_cache_treffer_neuer_name_aus_dateiname(env, monkeypatch):
    # Titel aus dem Dateinamen des ersten Hochladers darf nicht beim nächsten auftauchen
    import main
    raw = gpx(BERLIN, MUENCHEN)
    monkeypatch.setattr(main.analyze, "analyze", lambda raw, fn, *a, **k: {"stats": {"km": 500, "source": "lokal"}, "name": fn.rsplit(".", 1)[0]})
    out = main._submit(raw, "Besuch bei Anna.gpx", {"rig": True}, "olaf@x")
    main._run(out["job"], raw, "Besuch bei Anna.gpx", {"rig": True})
    c, h = client()
    jid = upload(c, h, raw, "tour.gpx")
    res = c.get(f"/api/jobs/{jid}", headers=h).json()["result"]
    assert res["name"] == "tour" and res["file"] == "tour.gpx" and "Anna" not in json.dumps(res)


def test_cache_kaputt_blockiert_nicht(env, monkeypatch):
    # Fehler im Zwischenspeicher verhindern nie die Analyse
    import main
    monkeypatch.setattr(main.resultcache, "load", lambda k: (_ for _ in ()).throw(RuntimeError("kaputt")))
    c, h = client()
    upload(c, h, gpx(BERLIN, MUENCHEN))
    assert len(env["pool"].calls) == 1


def test_cache_treffer_gleicher_login_kein_neuer_job(env):
    # wiederholte Treffer legen keine neuen Ergebniskopien im Speicher an
    import main
    raw = gpx(BERLIN, MUENCHEN)
    resultcache.store(resultcache.key(raw, {"rig": True}, main._cache_stamp()), {"stats": {"km": 500, "source": "lokal"}})
    c, h = client()
    a = upload(c, h, raw, "a.gpx")
    b = upload(c, h, raw, "a.gpx")
    assert a == b and len(main.jobs) == 1


def test_grenze_vor_dem_lesen(env, monkeypatch):
    # über der Grenze wird die GPX gar nicht erst gelesen
    import main
    for i in range(3):
        main.jobs[f"x{i}"] = {"state": "queued", "t": __import__("time").time(), "by": "olaf@x", "lane": "lokal"}
    calls = []
    monkeypatch.setattr(main.jobqueue, "route_km", lambda raw, pts=None: calls.append(1) or 0.0)
    c, h = client()
    r = c.post("/api/analyze", files={"file": ("t.gpx", gpx(BERLIN, MUENCHEN), "application/gpx+xml")}, headers=h)
    assert r.status_code == 429 and calls == []


def test_fehler_nach_fertig_bleibt_fertig(env, monkeypatch):
    # Fehler beim Zwischenspeichern macht eine fertige Analyse nicht kaputt
    import main
    raw = gpx(BERLIN, MUENCHEN)
    monkeypatch.setattr(main.analyze, "analyze", lambda *a, **k: {"stats": {"km": 500, "source": "lokal"}})
    monkeypatch.setattr(main.resultcache, "store", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("voll")))
    out = main._submit(raw, "t.gpx", {"rig": True}, "olaf@x")
    main._run(out["job"], raw, "t.gpx", {"rig": True})
    assert main.jobs[out["job"]]["state"] == "done"


def test_wartetext_mit_kopie_der_jobs(env, monkeypatch):
    # wait_text bekommt eine unter der Sperre gezogene Kopie
    import main
    seen = []
    monkeypatch.setattr(main.jobqueue, "wait_text",
                        lambda jobs, jid, slots: seen.append(jobs is main.jobs) or "Platz 1 in der Warteschlange · noch unter 1 Min.")
    c, h = client()
    jid = upload(c, h, gpx(BERLIN, MUENCHEN))
    c.get(f"/api/jobs/{jid}", headers=h)
    assert seen == [False]
