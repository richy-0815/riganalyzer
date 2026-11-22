import re
import time

import pytest

import trips

B = {"lat": 52.52, "lon": 13.405, "name": "Berlin", "stop": False}
C = {"lat": 43.847, "lon": 6.513, "name": "Castellane", "stop": False}


def test_roundtrip(data_dir):
    t = trips.save("richy", {"name": "Verdon", "vehicle": {"height": 3.0}, "stops": [B, C]})
    assert re.match(trips.ID_RE, t["id"])
    got = trips.get("richy", t["id"])
    assert got["name"] == "Verdon" and got["vehicle"]["height"] == 3.0
    assert [s["name"] for s in got["stops"]] == ["Berlin", "Castellane"]
    assert [x["id"] for x in trips.list_("richy")] == [t["id"]]


def test_update_keeps_id_and_created(data_dir):
    t = trips.save("richy", {"name": "A", "stops": [B, C]})
    time.sleep(1.1)
    u = trips.save("richy", {"id": t["id"], "name": "B", "stops": [C, B]})
    assert u["id"] == t["id"] and u["created"] == t["created"] and u["updated"] > t["updated"]
    assert trips.get("richy", t["id"])["name"] == "B"


def test_other_user_sees_nothing(data_dir):
    t = trips.save("richy", {"stops": [B, C]})
    assert trips.get("olaf", t["id"]) is None and trips.list_("olaf") == []
    assert trips.delete("olaf", t["id"]) is False and trips.get("richy", t["id"])


def test_rejects_bad_input(data_dir):
    for bad in ({"stops": [B]}, {"stops": [B] * 26}, {"stops": [{"lat": 99, "lon": 0}, B]},
                {"name": "x" * 81, "stops": [B, C]}, {"stops": "nix"}):
        with pytest.raises(trips.TripError):
            trips.save("richy", bad)


def test_bad_id_is_none(data_dir):
    assert trips.get("richy", "../../users") is None
    assert trips.delete("richy", "../../users") is False


def test_vehicle_clamped_and_defaults(data_dir):
    v = trips.save("richy", {"vehicle": {"height": 9, "trailer": True}, "stops": [B, C]})["vehicle"]
    assert v["height"] == 5 and v["width"] == 2.35 and v["trailer"] is True and v["tank"] == 90


def test_delete(data_dir):
    t = trips.save("richy", {"stops": [B, C]})
    assert trips.delete("richy", t["id"]) is True and trips.get("richy", t["id"]) is None


def test_unnamed_stops_get_fallback_name(data_dir):
    t = trips.save("richy", {"stops": [{**B, "name": ""}, {**C, "name": None}]})
    assert [s["name"] for s in t["stops"]] == ["Punkt 1", "Punkt 2"]


def test_mode_stored_and_validated(data_dir):
    assert trips.save("richy", {"stops": [B, C], "mode": "noway"})["mode"] == "noway"
    assert trips.save("richy", {"stops": [B, C], "mode": "kaputt"})["mode"] == "fast"


def test_website_cons_is_consumption(data_dir):
    assert trips.save("richy", {"stops": [B, C], "vehicle": {"cons": 10}})["vehicle"]["consumption"] == 10


def test_stop_mode_kept_and_validated(data_dir):
    t = trips.save("richy", {"stops": [B, {**C, "mode": "noway"}, {**C, "mode": "kaputt"}]})
    assert t["stops"][1]["mode"] == "noway" and "mode" not in t["stops"][2]


def test_vehicle_zgg_kept_and_defaulted(data_dir):
    t = trips.save("richy", {"name": "A", "vehicle": {"zgg": 3.2}, "stops": [B, C]})
    assert trips.get("richy", t["id"])["vehicle"]["zgg"] == 3.2
    t = trips.save("richy", {"name": "B", "vehicle": {}, "stops": [B, C]})
    assert trips.get("richy", t["id"])["vehicle"]["zgg"] == 3.5
    t = trips.save("richy", {"name": "C", "vehicle": {"zgg": 99}, "stops": [B, C]})
    assert trips.get("richy", t["id"])["vehicle"]["zgg"] == 12
