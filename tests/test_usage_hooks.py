import datetime as dt

import pytest

import auth
import events
import package
import planner
import usage
from test_api_trips import STOPS, _fake_package, basic, client as _client  # noqa: F401
from test_package import fake_steps, tid  # noqa: F401


@pytest.fixture
def client(_client):  # noqa: F811 – Fixture (Import oben)
    auth._fails.clear()   # andere Tests lassen Fehlversuche je IP stehen -> 429
    return _client


def _month_user(user="richy"):
    return usage.month(dt.datetime.now().strftime("%Y-%m"))["users"].get(user, {})


def test_download_counts_range_length(client, data_dir):
    trip_id = client.post("/api/trips", headers=basic("richy"), json={"stops": STOPS}).json()["trip"]["id"]
    _fake_package(data_dir, "richy", trip_id)
    url = f"/api/trips/{trip_id}/package/strassen.tar"
    client.get(url, headers={**basic("richy"), "range": "bytes=10-19"})
    assert _month_user()["dl_bytes"] == 10
    client.get(url, headers=basic("richy"))
    assert _month_user()["dl_bytes"] == 110
    client.get(url, headers={**basic("richy"), "range": "bytes=90-"})
    assert _month_user()["dl_bytes"] == 120
    client.get(url, headers={**basic("richy"), "range": "kaputt"})
    assert _month_user()["dl_bytes"] == 220


def test_analysis_counts_but_not_demo(data_dir):
    import main
    main._event_analysis({"by": "olaf@x"}, "a.gpx", 10, 3.5, {"rig": True})
    u = usage.month(dt.datetime.now().strftime("%Y-%m"))["users"]["olaf"]
    assert u["analyses"] == 1 and u["analyse_sec"] == 3.5 and u["app"] == "rig"
    main._event_analysis({"by": "demo@x", "demo": True}, "a.gpx", 10, 3.5, {})
    assert "demo" not in usage.month(dt.datetime.now().strftime("%Y-%m"))["users"]


def test_plan_counts(client, monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda *a, **k: {"costing": "truck", "km": 5})
    r = client.post("/api/plan", headers=basic("richy"), json={"points": [[1, 2], [3, 4]]})
    assert r.status_code == 200
    u = _month_user()
    assert u["plans"] == 1 and u["plan_sec"] >= 0


def test_nav_route_counts(client, monkeypatch):
    monkeypatch.setattr(planner, "nav_route", lambda *a, **k: (200, b"{}", "truck", None))
    r = client.post("/api/nav/route", headers=basic("richy"), json={"locations": []})
    assert r.status_code == 200
    assert _month_user()["routes"] == 1


def test_build_counts_minutes_and_bytes(tid):   # noqa: F811 – Fixture (Import oben)
    m = package.build("richy", tid, fake_steps())
    u = _month_user()
    assert u["build_min"] > 0 and u["build_bytes"] == sum(f["size"] for f in m["files"])


def test_seen_marks_active_day(client):
    client.get("/api/trips", headers=basic("richy"))
    assert _month_user()["days"] == [dt.date.today().isoformat()]


def test_usage_tick_backfills_once_and_snapshots(data_dir, monkeypatch):
    import main
    now = dt.datetime(2026, 11, 6, 9, 0)
    days = []

    def read_day(day):
        days.append(day.isoformat())
        return [{"user": "a", "app": "rig", "kind": "analyse", "sec": 2, "t": dt.datetime(2026, 11, 2, 10).timestamp()}]
    monkeypatch.setattr(events, "read_day", read_day)
    monkeypatch.setattr(main, "_usage_backfilled", False)
    (data_dir / "trips" / "a").mkdir(parents=True)
    (data_dir / "trips" / "a" / "x.json").write_bytes(b"x" * 1000)
    main._usage_tick(now)
    assert days == ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    u = usage.month("2026-11")["users"]["a"]
    assert u["analyses"] == 5 and u["storage_gbd"] > 0
    main._usage_tick(now)
    assert len(days) == 5   # Backfill nur einmal je Prozess
    assert usage.month("2026-11")["users"]["a"]["analyses"] == 5


def test_usage_tick_first_of_month_no_events(data_dir, monkeypatch):
    import main
    monkeypatch.setattr(events, "read_day", lambda d: pytest.fail("kein Tag vor dem 1."))
    monkeypatch.setattr(main, "_usage_backfilled", False)
    main._usage_tick(dt.datetime(2026, 11, 1, 9, 0))


def test_download_count_never_breaks_download(client, data_dir, monkeypatch):
    import rig_routes
    assert rig_routes._count_download("richy", None, str(data_dir / "fehlt")) is None   # Datei weg -> kein Wurf
