"""RigNavi bekommt Fahrpausen und Unterwegs-Orte (0.48.0, Ticket #14): Reise speichert die Pausen-Wahl,
reise.json/Reisepaket und GET /api/trips/{id}/way liefern Sehenswertes, Einkehr und Service."""
import pytest

import reisedaten
import trips
from test_api_trips import STOPS, basic, client  # noqa: F401  (Fixture)

B = {"lat": 52.52, "lon": 13.405, "name": "Berlin", "stop": False}
C = {"lat": 43.847, "lon": 6.513, "name": "Castellane", "stop": False}
TRIP = {"name": "T", "vehicle": {}, "stops": [{"lat": 48, "lon": 11, "name": "A", "stop": False},
                                              {"lat": 48.2, "lon": 11.2, "name": "C", "stop": False}]}


def _p(group, km, score=0.0, label="X", name="", **kw):
    return {"group": group, "label": label, "name": name, "lat": 48.0, "lon": 11.0 + km / 100, "km": km, "off": 120,
            "score": score, "wiki": False, "flags": {"hours": "Mo-Su 08:00-18:00", "kind": kw.get("kind")}, "osm": f"node/{km}"}


def test_trip_saves_pause(data_dir):
    t = trips.save("richy", {"name": "V", "stops": [B, C], "pause": "2h20"})
    assert trips.get("richy", t["id"])["pause"] == "2h20"


def test_trip_keeps_pause_when_missing(data_dir):
    t = trips.save("richy", {"name": "V", "stops": [B, C], "pause": "none"})
    trips.save("richy", {"id": t["id"], "name": "V2", "stops": [B, C]})
    assert trips.get("richy", t["id"])["pause"] == "none"


@pytest.mark.parametrize("v", ["x", 5, None, True])
def test_trip_invalid_pause_is_auto(data_dir, v):
    t = trips.save("richy", {"name": "V", "stops": [B, C], "pause": v})
    assert trips.get("richy", t["id"])["pause"] == "auto"


def test_trip_without_pause_has_none(data_dir):
    t = trips.save("richy", {"name": "V", "stops": [B, C]})
    assert "pause" not in trips.get("richy", t["id"])


def test_reisedaten_has_pause():
    a = {"pois": [], "segments": []}
    assert reisedaten.build({**TRIP, "pause": "2h20"}, {}, a)["pause"] == "2h20"
    assert reisedaten.build(TRIP, {}, a)["pause"] == "auto"


def test_build_includes_way_groups():
    a = {"pois": [_p("sight", 5, 2.0, "Burg/Schloss", "Burg"), _p("food", 6, label="Café"), _p("service", 7, kind="gas"),
                  _p("pause", 8), _p("rest", 9)], "segments": []}
    out = reisedaten.build(TRIP, {}, a)
    groups = sorted(p["group"] for p in out["pois"])
    assert groups == ["food", "service", "sight"]
    s = next(p for p in out["pois"] if p["group"] == "sight")
    assert s["name"] == "Burg" and s["label"] == "Burg/Schloss" and s["off"] == 120 and s["wiki"] is False
    assert s["flags"]["hours"] == "Mo-Su 08:00-18:00" and s["osm"] == "node/5"


def test_way_cap_by_score():
    a = {"pois": [_p("sight", float(i), score=float(i % 7)) for i in range(350)], "segments": []}
    out = reisedaten.way_pois([a])
    assert len(out) == reisedaten.WAY_MAX == 300
    assert min(p["score"] for p in out) >= 1.0   # die schlechtesten (score 0) fallen zuerst weg
    assert [p["km"] for p in out] == sorted(p["km"] for p in out)


def test_way_km_offset():
    s1 = {"pois": [_p("food", 3)], "segments": [], "stats": {"km": 100}}
    s2 = {"pois": [_p("food", 4)], "segments": [], "stats": {"km": 50}}
    assert [p["km"] for p in reisedaten.way_pois([s1, s2])] == [3, 104]


def test_way_without_groups():
    assert reisedaten.way_pois([{"pois": [_p("fuel", 1)], "segments": []}]) == []
    assert reisedaten.way_pois([{}]) == []


def test_way_endpoint(client, data_dir):  # noqa: F811
    r = client.post("/api/trips", headers=basic("richy"), json={"name": "V", "stops": STOPS, "pause": "2h20"})
    tid = r.json()["trip"]["id"]
    j = client.get(f"/api/trips/{tid}/way", headers=basic("richy")).json()
    assert j == {"pois": [], "pause": "2h20", "missing": "analysis"}
    trips.save_analysis("richy", tid, "0" * 16, {"pois": [_p("sight", 5, 1.0)], "segments": []})
    j = client.get(f"/api/trips/{tid}/way", headers=basic("richy")).json()
    assert [p["group"] for p in j["pois"]] == ["sight"] and "missing" not in j
    assert client.get(f"/api/trips/{tid}/way", headers=basic("olaf")).status_code == 404
    assert client.get(f"/api/trips/{tid}", headers=basic("richy")).json()["trip"]["pause"] == "2h20"
