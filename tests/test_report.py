"""Ereignis-Tagebuch + Tagesbericht (seit v0.16.0)."""
import datetime as dt
import json

import pytest

import events
import report

DAY = dt.date(2027, 5, 24)


@pytest.fixture
def ev(data_dir, monkeypatch):
    monkeypatch.setattr(events, "DIR", str(data_dir / "events"))
    return data_dir / "events"


def test_log_appends_jsonl_per_local_day(ev):
    events.log("login", "otto", "ride", ok=True, ip="1.2.3.4", now=dt.datetime(2027, 5, 24, 23, 30))
    events.log("analyse", "otto", "ride", name="Harz.gpx", km=159, now=dt.datetime(2027, 5, 25, 0, 10))
    assert [e["kind"] for e in events.read_day(DAY)] == ["login"]
    e = events.read_day(DAY + dt.timedelta(days=1))[0]
    assert e["user"] == "otto" and e["app"] == "ride" and e["name"] == "Harz.gpx" and e["km"] == 159


def test_never_stores_secrets(ev):
    events.log("admin", "bofh", None, target="finn", what="Passwort neu gesetzt", password="geheim", lat=50.1, lon=8.6,
               now=dt.datetime(2027, 5, 24, 9))
    raw = (ev / "2027-05-24.jsonl").read_text()
    assert "geheim" not in raw and "50.1" not in raw


def test_prune_keeps_90_days(ev):
    ev.mkdir(parents=True)
    for d in ("2027-01-01", "2027-05-01"):
        (ev / f"{d}.jsonl").write_text("{}\n")
    events.prune(today=DAY)
    assert sorted(p.name for p in ev.iterdir()) == ["2027-05-01.jsonl"]


def _evs():
    t = lambda h, m=0: dt.datetime(2027, 5, 24, h, m).timestamp()  # noqa: E731
    return [
        {"t": t(8), "kind": "login", "user": "hugo", "app": "ride", "ok": True, "ip": "80.1.2.3", "device": "Browser"},
        {"t": t(8, 5), "kind": "analyse", "user": "hugo", "app": "ride", "name": "Harz-Tour.gpx", "km": 159.4, "sec": 3.2},
        {"t": t(8, 9), "kind": "analyse", "user": "hugo", "app": "ride", "name": "Altmark.gpx", "km": 357, "sec": 4},
        {"t": t(8, 20), "kind": "beispiel", "user": "hugo", "app": "ride", "name": "xjrnord"},
        {"t": t(9), "kind": "planung", "user": "richy", "app": "rig", "km": 412, "mode": "fast"},
        {"t": t(9, 30), "kind": "login", "user": "-", "app": "ride", "ok": False, "ip": "203.0.113.9", "device": "Browser", "tried": "admin"},
        {"t": t(10), "kind": "admin", "user": "bofh", "target": "vivian", "what": "angelegt in riders_only_tiemo"},
        {"t": t(11), "kind": "fehler", "logger": "rig", "msg": "Hoehenprofil nicht verfuegbar: x"},
        {"t": t(11, 1), "kind": "fehler", "logger": "rig", "msg": "Hoehenprofil nicht verfuegbar: x"},
        {"t": t(12), "kind": "navi", "user": "richy", "app": "rig", "dest": "Castellane"},
    ]


def test_report_sections():
    subject, text = report.build(DAY, _evs(), {"version": "0.16.0", "osm": "25.10.2026", "disk_free_gb": 120.5,
                                               "valhalla": "ok", "errors_total": 2})
    assert subject == "RideAnalyzer/RigAnalyzer – Tagesbericht 24.05.2027: 2 aktiv, 2 Analysen, 1 Planung"
    assert "Ride: 1 aktiv · 2 Analysen · 1 Beispiel" in text
    assert "Rig: 1 aktiv · 1 Planung · 1 Navi-Fahrt" in text
    assert "hugo · Ride · 1 Anmeldung · 2 Analysen (Harz-Tour 159 km, Altmark 357 km) · 1 Beispiel (xjrnord)" in text
    assert "richy · Rig · 1 Planung (412 km) · 1 Navi-Fahrt (Castellane)" in text
    assert "1 Fehlversuch: admin von 203.0.113.9 (09:30)" in text
    assert "bofh → vivian: angelegt in riders_only_tiemo (10:00)" in text
    assert "2× rig: Hoehenprofil nicht verfuegbar: x" in text
    assert "Version 0.16.0 · Kartendaten 25.10.2026 · Valhalla ok · 120 GB frei" in text


def test_quiet_day_has_no_report():
    assert report.build(DAY, [], {}) is None
    assert report.build(DAY, [{"t": 0, "kind": "fehler", "logger": "x", "msg": "y"}], {}) is not None   # Fehler immer melden


def test_due_once_per_day(ev):
    morning = dt.datetime(2027, 5, 25, 7, 0, 5)
    assert report.due(morning) == DAY
    report.mark_sent(DAY)
    assert report.due(morning) is None
    assert report.due(dt.datetime(2027, 5, 25, 6, 59)) is None          # vor 7 Uhr nicht
    assert report.due(dt.datetime(2027, 5, 25, 12, 30)) is None          # nach 12 Uhr nicht nachholen


def test_admin_preview(data_dir, monkeypatch, ev):
    from test_group_admin import env, login  # noqa: F401
    events.log("analyse", "otto", "ride", name="X.gpx", km=10, now=dt.datetime.now() - dt.timedelta(days=1))
    import auth
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {"bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True},
                                                               "gina": {"hash": h, "apps": ["ride"], "admin": {"apps": ["ride"], "groups": ["g1"]}}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    c, h = login()
    r = c.get("/api/admin/report", headers=h)
    assert r.status_code == 200 and "otto" in r.text and "X 10 km" in r.text
    g, gh = login("gina")
    assert g.get("/api/admin/report", headers=gh).status_code == 403


# ---------------------------------------------------------------- Platz auf dem Server
G = 1e9


def _space(free, dirs, files, t=dt.datetime(2027, 5, 25, 6, 40), docker=()):
    return {"t": int(t.timestamp()), "day": t.date().isoformat(), "total": 64 * G, "free": free,
            "dirs": dirs, "files": {p: {"size": s, "mtime": 0} for p, s in files.items()}, "docker": list(docker)}


def test_space_section_with_changes_since_yesterday():
    prev = _space(14 * G, {"/var/lib/docker": 9 * G, "/srv/riganalyzer/valhalla": 11 * G},
                  {"/srv/riganalyzer/valhalla/valhalla_tiles.tar": 10.5 * G, "/var/tmp/alt.img": 0.8 * G},
                  t=dt.datetime(2027, 5, 24, 6, 40))
    now = _space(11.5 * G, {"/var/lib/docker": 11.2 * G, "/srv/riganalyzer/valhalla": 11 * G, "/srv/neu": 1 * G},
                 {"/srv/riganalyzer/valhalla/valhalla_tiles.tar": 10.5 * G, "/srv/riganalyzer/data/osm/eu.db": 2.1 * G},
                 docker=[{"type": "Images", "size": "9.8GB", "reclaimable": "6.1GB (62%)"},
                         {"type": "Containers", "size": "12MB", "reclaimable": "0B (0%)"}])
    lines = report.space_lines(now, prev)
    text = "\n".join(lines)
    assert lines[0] == "PLATZ AUF DEM SERVER (Stand 25.05. 06:40)"
    assert "  11,5 GB frei von 64,0 GB (18 %), seit gestern -2,5 GB" in text
    assert "11,2 GB  /var/lib/docker  (+2,2 GB)" in text
    assert "11,0 GB  /srv/riganalyzer/valhalla\n" in text + "\n"          # unveraendert: kein Vermerk
    assert "1,0 GB  /srv/neu  (neu)" in text
    assert "2,1 GB  /srv/riganalyzer/data/osm/eu.db  NEU" in text
    assert "10,5 GB  /srv/riganalyzer/valhalla/valhalla_tiles.tar\n" in text + "\n"
    assert "Seit gestern weg: /var/tmp/alt.img" in text
    assert "Docker freigebbar: Images 6.1GB (62%) von 9.8GB  → ra platz" in text
    assert "Containers" not in text


def test_space_first_day_without_yesterday_marks_nothing_new():
    text = "\n".join(report.space_lines(_space(30 * G, {"/a/b/c": 2 * G}, {"/a/b/c/x": 1 * G})))
    assert "30,0 GB frei von 64,0 GB (47 %)" in text and "seit gestern" not in text
    assert "NEU" not in text and "(neu)" not in text


def test_space_missing_means_no_section():
    assert report.space_lines(None) == []
    _, text = report.build(DAY, _evs(), {}, (None, None))
    assert "PLATZ AUF DEM SERVER" not in text


def test_space_in_report_and_low_space_forces_report():
    now = _space(8 * G, {"/var/lib/docker": 20 * G}, {})
    subject, text = report.build(DAY, _evs(), {"version": "x"}, (now, None))
    assert "PLATZ AUF DEM SERVER" in text and "  ⚠ 8,0 GB frei" in text
    assert subject.endswith("· ⚠ Server nur 8,0 GB frei")
    assert report.build(DAY, [], {}, (now, None)) is not None                    # ruhiger Tag, aber Platz knapp
    assert report.build(DAY, [], {}, (_space(30 * G, {}, {}), None)) is None     # ruhiger Tag, Platz ok
