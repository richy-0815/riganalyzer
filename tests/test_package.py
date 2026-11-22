import hashlib
import json
import os

import pytest

import package
import planner
import trips

STOPS = [{"lat": 45.70, "lon": 4.85, "name": "Lyon"}, {"lat": 43.847, "lon": 6.513, "name": "Castellane"}]
GPX = ('<gpx><trk><name>x</name><trkseg><trkpt lat="45.700000" lon="4.850000"/>'
       '<trkpt lat="43.847000" lon="6.513000"/></trkseg></trk></gpx>')


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def fake_steps(**over):
    def style(d):
        _write(f"{d}/stil/hell.json", b"{}")
        _write(f"{d}/stil/dunkel.json", b"{}")
    s = dict(
        plan=lambda trip: {"gpx": GPX, "km": 334.0, "minutes": 300, "stages": [{"km": 334.0, "min": 300}]},
        roads=lambda dst, pts: _write(dst, b"tiles" * 10) or {"tiles": 1, "bytes": 50},
        map=lambda out, ring: _write(out, b"PMTiles") or "20261025.pmtiles",
        style=style,
        analyze=lambda gpx: {"rig": {"restrictions": [], "steep": [], "hairpins": [], "countries": []}, "pois": [],
                             "stats": {"km": 334.0}},
        roads_date=lambda: "2026-10-22",
    )
    s.update(over)
    return package.Steps(**s)


@pytest.fixture
def tid(data_dir):
    return trips.save("richy", {"name": "Lyon – Castellane", "stops": STOPS})["id"]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_manifest_complete_and_checksums(tid):
    m = package.build("richy", tid, fake_steps())
    d = package.current_dir("richy", tid)
    assert m["format"] == 1 and m["trip_id"] == tid and m["name"] == "Lyon – Castellane"
    names = {f["name"] for f in m["files"]}
    assert {"strassen.tar", "karte.pmtiles", "reise.json", "stil/hell.json", "stil/dunkel.json"} <= names
    assert "paket.json" not in names
    assert all(sha(os.path.join(d, f["name"])) == f["sha256"] and os.path.getsize(os.path.join(d, f["name"])) == f["size"]
               for f in m["files"])
    assert json.load(open(os.path.join(d, "paket.json"))) == m
    assert len(m["polygon"]) > 10 and m["data_dates"] == {"strassen": "2026-10-22", "karte": "20261025.pmtiles"}
    st = package.status("richy", tid)
    assert st["state"] == "ready" and st["size"] == sum(f["size"] for f in m["files"]) and st["built"] == m["built"]


def test_reise_json_written(tid):
    package.build("richy", tid, fake_steps())
    r = json.load(open(os.path.join(package.current_dir("richy", tid), "reise.json")))
    assert r["stages"][0]["to"] == "Castellane"


def test_failure_keeps_previous_package(tid):
    package.build("richy", tid, fake_steps())
    old = package.current_dir("richy", tid)

    def boom(out, ring):
        raise RuntimeError("Protomaps nicht erreichbar")
    with pytest.raises(RuntimeError):
        package.build("richy", tid, fake_steps(map=boom))
    assert package.current_dir("richy", tid) == old
    st = package.status("richy", tid)
    assert st["state"] == "error" and "Protomaps" in st["msg"]
    assert not [n for n in os.listdir(os.path.dirname(old)) if n.endswith(".tmp")]


def test_new_build_removes_older(tid):
    package.build("richy", tid, fake_steps())
    package.build("richy", tid, fake_steps())
    base = os.path.dirname(package.current_dir("richy", tid))
    assert len([n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n))]) == 1


def test_outside_area_error_message(tid):
    def outside(trip):
        raise planner.PlanError("Keine Route gefunden. Liegen alle Punkte an einer Straße im Planungsgebiet (DACH)?")
    with pytest.raises(planner.PlanError):
        package.build("richy", tid, fake_steps(plan=outside))
    st = package.status("richy", tid)
    assert st["state"] == "error" and "Planungsgebiet" in st["msg"] and package.current_dir("richy", tid) is None


def test_unknown_trip(data_dir):
    with pytest.raises(KeyError):
        package.build("richy", "000000000000", fake_steps())
    assert package.status("richy", "000000000000")["state"] == "none"


def test_delete_removes_packages(tid):
    package.build("richy", tid, fake_steps())
    package.delete("richy", tid)
    assert package.current_dir("richy", tid) is None and package.status("richy", tid)["state"] == "none"


def test_too_long_stage_is_clear_error(tid):
    def long_plan(trip):
        return {"gpx": GPX, "stages": [{"km": 1400.0, "min": 900}], "too_long": True, "max_km": 1000}

    with pytest.raises(planner.PlanError):
        package.build("richy", tid, fake_steps(plan=long_plan))
    assert "länger als 1000 km" in package.status("richy", tid)["msg"]


def test_real_analyze_step_splits_stages(data_dir, monkeypatch):
    import os as _os
    _os.environ.setdefault("DEMO_WARM", "0")
    _os.environ.setdefault("OSM_AUTOBUILD", "0")
    import analyze
    seen = []
    monkeypatch.setattr(analyze, "analyze", lambda raw, fn, *a, **k: seen.append(fn) or
                        {"rig": {"restrictions": [], "steep": [], "hairpins": [], "countries": []}, "pois": [], "stats": {"km": 1}})
    gpx = ('<gpx xmlns="http://www.topografix.com/GPX/1/1">'
           '<trk><name>Etappe 1</name><trkseg><trkpt lat="45.7" lon="4.85"/><trkpt lat="45.0" lon="5.5"/></trkseg></trk>'
           '<trk><name>Etappe 2</name><trkseg><trkpt lat="45.0" lon="5.5"/><trkpt lat="43.847" lon="6.513"/></trkseg></trk></gpx>')
    res = package.real_steps().analyze(gpx.encode())
    assert res["trip"] is True and len(res["stages"]) == 2 and seen == ["etappe1.gpx", "etappe2.gpx"]


def test_mode_and_holes_in_manifest(data_dir):
    tid = trips.save("richy", {"stops": STOPS, "mode": "noway"})["id"]
    seen = {}
    m = package.build("richy", tid, fake_steps(plan=lambda trip: seen.update(mode=trip["mode"]) or
                                                {"gpx": GPX, "stages": [{"km": 1, "min": 1}]}))
    assert seen["mode"] == "noway" and m["mode"] == "noway" and isinstance(m["holes"], list)


def test_no_space_on_lisa_is_clear_error(tid, monkeypatch):
    import collections
    monkeypatch.setattr(package.shutil, "disk_usage", lambda p: collections.namedtuple("U", "total used free")(1, 1, 2 * 10**9))
    with pytest.raises(RuntimeError):
        package.build("richy", tid, fake_steps())
    assert "Platz" in package.status("richy", tid)["msg"]


def test_only_newest_packages_per_user_kept(data_dir, monkeypatch):
    monkeypatch.setattr(package, "MAX_PACKAGES_PER_USER", 2)
    ids = [trips.save("richy", {"name": f"R{i}", "stops": STOPS})["id"] for i in range(3)]
    for i in ids:
        package.build("richy", i, fake_steps())
    assert package.current_dir("richy", ids[0]) is None
    assert package.current_dir("richy", ids[1]) and package.current_dir("richy", ids[2])


def test_recover_after_restart(tid):
    package._set_status("richy", tid, state="building", msg="Karte", progress=40)
    tmp = os.path.join(package._base("richy", tid), "20260101000000.tmp")
    os.makedirs(tmp)
    package.recover()
    st = package.status("richy", tid)
    assert st["state"] == "error" and "unterbrochen" in st["msg"] and not os.path.exists(tmp)


def test_status_writes_are_thread_safe(tid):
    import threading
    errs = []

    def hammer():
        for i in range(200):
            try:
                package._set_status("richy", tid, state="building", msg="x" * (i % 50), progress=i)
                package.status("richy", tid)
            except Exception as e:  # noqa: BLE001
                errs.append(e)
    th = [threading.Thread(target=hammer) for _ in range(4)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert errs == []


def test_manifest_leg_modes(data_dir):
    tid = trips.save("richy", {"stops": STOPS + [{"lat": 43.7486, "lon": 6.0911, "name": "Riez", "mode": "noway"}],
                               "mode": "fast"})["id"]
    m = package.build("richy", tid, fake_steps(plan=lambda trip: {"gpx": GPX, "stages": [{"km": 1, "min": 1}],
                                                                  "leg_modes": ["fast", "noway"]}))
    assert m["leg_modes"] == ["fast", "noway"]


# ---------------------------------------------------------------- Schritte, Laufzeit, Warteplatz
def test_status_lists_steps_and_tracks_current_step(tid):
    seen = []

    def roads(dst, pts):
        seen.append(package.status("richy", tid))
        _write(dst, b"tiles")
    package.build("richy", tid, fake_steps(roads=roads))
    st = seen[0]
    assert st["state"] == "building" and st["step"] == 1 and st["progress"] == 20
    assert st["msg"] == "Straßendaten werden ausgeschnitten" and st["elapsed"] >= 0
    assert [s["done"] for s in st["steps"]][:2] == ["Route berechnet", "Straßendaten ausgeschnitten"]
    assert len(st["steps"]) == len(package.STEPS) == 5
    done = package.status("richy", tid)
    assert done["state"] == "ready" and done["took"] >= 0 and "elapsed" not in done


def test_error_remembers_failed_step(tid):
    def bad_map(out, ring):
        raise RuntimeError("Karte kaputt")
    with pytest.raises(RuntimeError):
        package.build("richy", tid, fake_steps(map=bad_map))
    st = package.status("richy", tid)
    assert st["state"] == "error" and st["step"] == 2 and "Karte kaputt" in st["msg"]


def test_queued_shows_place_in_line(tid, monkeypatch):
    other = trips.save("richy", {"name": "zweite", "stops": STOPS})["id"]
    monkeypatch.setattr(package, "_worker", object())   # kein echter Bau-Thread
    monkeypatch.setattr(package, "_q", __import__("queue").Queue())
    monkeypatch.setattr(package, "_pending", set())
    monkeypatch.setattr(package, "_current", ("olaf", "x"))
    package.request("richy", tid)
    package.request("richy", other)
    a, b = package.status("richy", tid), package.status("richy", other)
    assert a["state"] == b["state"] == "queued"
    assert (a["ahead"], b["ahead"]) == (1, 2) and a["waiting"] >= 0


# ---------------------------------------------------------------- Restzeit aus frueheren Bauten
def test_eta_from_samples_scaled_by_km():
    samples = [{"km": 100, "steps": [2, 10, 20, 6, 2]}, {"km": 300, "steps": [6, 30, 60, 18, 6]}]
    # 200 km, Schritt 2 (Karte) seit 10 s: Karte 40 s erwartet -> 30 s Rest + Gespann 12 + Abschluss 4
    assert package.eta(2, 10, 200, samples) == 46
    assert package.eta(2, 999, 200, samples) == 16            # laufender Schritt laenger als erwartet: nur der Rest
    assert package.eta(0, 0, None, samples) == 80              # vor der Planung (km unbekannt): Median der Sekunden je Schritt
    assert package.eta(2, 0, 200, []) is None and package.eta(None, 0, 200, samples) is None


def test_finished_build_records_timing_and_status_has_eta(tid, data_dir):
    package.build("richy", tid, fake_steps())
    data = json.load(open(data_dir / "package-timing.json"))
    assert len(data) == 1 and data[0]["km"] == 334.0 and len(data[0]["steps"]) == len(package.STEPS)
    seen = []

    def roads(dst, pts):
        seen.append(package.status("richy", tid))
        _write(dst, b"tiles")
    package.build("richy", tid, fake_steps(roads=roads))
    assert seen[0]["km"] == 334.0 and isinstance(seen[0]["eta"], int) and seen[0]["eta"] >= 0
    assert len(json.load(open(data_dir / "package-timing.json"))) == 2


def test_timing_kept_short_and_outside_user_folders(tid, data_dir, monkeypatch):
    monkeypatch.setattr(package, "TIMING_KEEP", 3)
    for _ in range(5):
        package._record_timing(100, [1, 2, 3, 4, 5])
    assert len(package._load_timing()) == 3
    package._record_timing(100, [1, 2])                         # falsche Laenge wird ignoriert
    assert len(package._load_timing()) == 3
    package.recover()                                           # Datei liegt nicht unter packages/
