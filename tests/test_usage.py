import datetime as dt
import threading

import pytest

import usage


def test_add_counts_and_days(data_dir):
    t = dt.datetime(2026, 11, 6, 10)
    usage.add("Richy", "rig", now=t, dl_bytes=100, analyses=1)
    usage.add("richy", "rig", now=t + dt.timedelta(days=1), dl_bytes=50)
    u = usage.month("2026-11")["users"]["richy"]
    assert u["dl_bytes"] == 150 and u["analyses"] == 1 and u["days"] == ["2026-11-06", "2026-11-07"] and u["app"] == "rig"


def test_month_rollover_and_months(data_dir):
    usage.add("a", "ride", now=dt.datetime(2026, 10, 31, 23), plans=1)
    usage.add("a", "ride", now=dt.datetime(2026, 11, 1, 0, 5), plans=1)
    assert usage.month("2026-10")["users"]["a"]["plans"] == 1 and usage.months() == ["2026-11", "2026-10"]


def test_threads_lose_nothing(data_dir):
    ts = [threading.Thread(target=lambda: [usage.add("x", "rig", routes=1) for _ in range(50)]) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert usage.month(dt.date.today().strftime("%Y-%m"))["users"]["x"]["routes"] == 400


def test_write_error_is_silent(data_dir, monkeypatch):
    monkeypatch.setattr(usage.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("voll")))
    usage.add("x", "rig", routes=1)   # darf nicht werfen


def test_backfill_once(data_dir):
    evs = [{"kind": "analyse", "user": "olaf", "app": "rig", "sec": 12.5}, {"kind": "planung", "user": "olaf", "app": "rig"},
           {"kind": "navi", "user": "olaf", "app": "rig"}, {"kind": "login", "user": "olaf", "app": "rig", "ok": True, "t": 1793952000}]
    usage.backfill("2026-11", evs)
    usage.backfill("2026-11", evs)
    u = usage.month("2026-11")["users"]["olaf"]
    assert (u["analyses"], u["analyse_sec"], u["plans"], u["routes"]) == (1, 12.5, 1, 1) and u["days"]


def test_storage_snapshot_once_per_day(data_dir):
    (data_dir / "packages" / "richy" / "t1").mkdir(parents=True)
    (data_dir / "packages" / "richy" / "t1" / "f").write_bytes(b"x" * 2_000_000)
    now = dt.datetime(2026, 11, 6, 3)
    usage.storage_snapshot(now)
    usage.storage_snapshot(now)
    assert usage.month("2026-11")["users"]["richy"]["storage_gbd"] == pytest.approx(0.002)


def test_storage_walk_outside_lock(data_dir, monkeypatch):
    (data_dir / "packages" / "richy").mkdir(parents=True)
    (data_dir / "packages" / "richy" / "f").write_bytes(b"x" * 10)
    seen = []
    real = usage._du
    monkeypatch.setattr(usage, "_du", lambda p: seen.append(usage._lock.locked()) or real(p))
    usage.storage_snapshot(dt.datetime(2026, 11, 6, 3))
    assert seen == [False]


def test_backfill_skips_events_already_counted_live(data_dir):
    live = dt.datetime(2026, 12, 2, 10)
    usage.add("olaf", "rig", now=live, analyses=1)          # Monatsdatei ohne "backfilled", since = 2.12. 10:00
    since = live.timestamp()
    evs = [{"kind": "analyse", "user": "olaf", "app": "rig", "sec": 1, "t": since + 60},     # nach since: schon live gezaehlt
           {"kind": "analyse", "user": "olaf", "app": "rig", "sec": 1, "t": since - 3600}]   # vor since: nachtragen
    usage.backfill("2026-12", evs)
    assert usage.month("2026-12")["users"]["olaf"]["analyses"] == 2


def test_backfill_without_since_takes_all(data_dir):
    usage.backfill("2026-12", [{"kind": "planung", "user": "olaf", "app": "rig", "t": 1796000000}])
    assert usage.month("2026-12")["users"]["olaf"]["plans"] == 1


def test_storage_snapshot_counts_snap_days(data_dir):
    (data_dir / "packages" / "richy" / "t1").mkdir(parents=True)
    (data_dir / "packages" / "richy" / "t1" / "f").write_bytes(b"x" * 1000)
    usage.storage_snapshot(dt.datetime(2026, 11, 6, 3))
    usage.storage_snapshot(dt.datetime(2026, 11, 7, 3))
    assert usage.month("2026-11")["users"]["richy"]["snaps"] == 2
