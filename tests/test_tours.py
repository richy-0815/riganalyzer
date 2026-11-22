"""Meine Touren (seit v0.26.0): kompletter Planer-Stand, fruehere Staende, Kopie – Speichern baut kein Paket."""
import time

import pytest

import package
import trips
from test_api_trips import basic, client  # noqa: F401  (Fixture client)

B = {"lat": 52.52, "lon": 13.405, "name": "Berlin", "stop": False}
C = {"lat": 43.847, "lon": 6.513, "name": "Castellane", "stop": False}
PLAN = {"v": 1, "pts": [B, C], "mode": "fast", "nights": "5", "skip": ["2:1"], "avoided": []}


def test_plan_and_note_kept_and_versions(data_dir):
    t = trips.save("richy", {"name": "Verdon", "stops": [B, C], "plan": PLAN, "note": "Fähre buchen"})
    assert t["plan"]["skip"] == ["2:1"] and t["note"] == "Fähre buchen"
    time.sleep(1.1)
    u = trips.save("richy", {"id": t["id"], "name": "Verdon 2", "stops": [C, B]})       # ohne plan: alter bleibt
    assert u["plan"] == PLAN and u["note"] == "Fähre buchen"
    vs = trips.versions("richy", t["id"])
    assert len(vs) == 1 and vs[0]["name"] == "Verdon"
    time.sleep(1.1)
    r = trips.restore("richy", t["id"], vs[0]["v"])
    assert r["name"] == "Verdon" and r["stops"][0]["name"] == "Berlin"
    assert [v["name"] for v in trips.versions("richy", t["id"])] == ["Verdon 2", "Verdon"]   # aktueller wurde Version
    assert [x["id"] for x in trips.list_("richy")] == [t["id"]]                           # Versionen sind keine Touren


def test_only_last_versions_kept(data_dir, monkeypatch):
    monkeypatch.setattr(trips, "KEEP_VERSIONS", 3)
    t = trips.save("richy", {"name": "x", "stops": [B, C]})
    for i in range(5):
        old = trips.get("richy", t["id"])
        old["updated"] = f"2026-01-0{i + 1}T10:00:00"
        trips._keep_version("richy", old)
    assert [v["updated"][:10] for v in trips.versions("richy", t["id"])] == ["2026-01-05", "2026-01-04", "2026-01-03"]


def test_copy_and_delete_with_versions(data_dir):
    t = trips.save("richy", {"name": "Verdon", "stops": [B, C], "plan": PLAN})
    trips.save("richy", {"id": t["id"], "name": "Verdon", "stops": [C, B]})
    c = trips.copy("richy", t["id"])
    assert c["id"] != t["id"] and c["name"] == "Kopie von Verdon" and c["plan"] == PLAN
    assert trips.delete("richy", t["id"]) and trips.versions("richy", t["id"]) == []
    assert trips.restore("richy", t["id"], "../../etc") is None


def test_plan_too_big(data_dir):
    with pytest.raises(trips.TripError):
        trips.save("richy", {"stops": [B, C], "plan": {"x": "y" * (trips.MAX_PLAN + 10)}})


def test_api_tour_save_builds_no_package(client, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    env = client
    built = []
    monkeypatch.setattr(package, "request", lambda user, tid: built.append(tid))
    h = basic("olaf")
    r = env.post("/api/tours", json={"name": "Verdon", "stops": [B, C], "plan": PLAN}, headers=h)
    assert r.status_code == 200 and built == []
    tid = r.json()["trip"]["id"]
    lst = env.get("/api/trips", headers=h).json()
    assert lst[0]["tour"] is True and lst[0]["from"] == "Berlin" and lst[0]["to"] == "Castellane"
    env.post("/api/tours", json={"id": tid, "name": "Verdon 2", "stops": [B, C], "plan": PLAN}, headers=h)
    vs = env.get(f"/api/trips/{tid}/versions", headers=h).json()["versions"]
    assert len(vs) == 1
    assert env.post(f"/api/trips/{tid}/restore", json={"v": vs[0]["v"]}, headers=h).json()["trip"]["name"] == "Verdon"
    assert env.post(f"/api/trips/{tid}/copy", headers=h).json()["trip"]["name"].startswith("Kopie von")
