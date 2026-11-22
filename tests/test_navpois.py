"""Tankstellen + Plaetze an einer Navi-Route fuer die iPad-App (v0.27.0)."""
import pytest

import navpois
from test_admin import basic, env  # noqa: F401  (Fixture env)

LINE = [[48.0 + i * 0.001, 8.0] for i in range(1000)]   # ~111 km nach Norden
VEH = {"height": 2.9, "length": 11.89, "weight": 5}


def node(i, lat, tags, lon=8.0):
    return {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": tags}


class FakeDB:
    def covers(self, lat, lon):
        return True

    def fuel_near(self, lat, lon, proj, radius):
        return [(node(1, 48.5, {"amenity": "fuel", "name": "Aral Mitte", "brand": "Aral"}), 80.0),
                (node(2, 48.1, {"amenity": "fuel", "name": "Gas", "fuel:diesel": "no"}), 50.0),
                (node(3, 48.2, {"amenity": "fuel", "name": "AS 24"}), 50.0),
                (node(4, 48.05, {"amenity": "fuel", "brand": "Total"}), 120.0)]

    def camps_near(self, lat, lon, proj, radius):
        return [(node(10, 48.9, {"tourism": "caravan_site", "name": "Stellplatz Nord", "maxlength": "12",
                                 "maxweight": "7.5", "maxheight": "3.5"}), 900.0),
                (node(11, 48.3, {"tourism": "caravan_site", "name": "Zu kurz", "maxlength": "8"}), 300.0),
                (node(12, 48.4, {"tourism": "camp_site", "name": "Privat", "access": "private"}), 300.0),
                (node(13, 48.6, {"tourism": "camp_site", "name": "Camping Ohne Maße"}), 2500.0)]


def test_find_sorts_by_km_and_filters():
    r = navpois.find(LINE, VEH, FakeDB())
    assert [f["name"] for f in r["fuel"]] == ["Total", "Aral Mitte"]       # kein Diesel / AS 24 raus
    assert r["fuel"][1]["km"] == pytest.approx(55.3, abs=0.2) and r["fuel"][0]["brand"] == "Total"
    assert [c["name"] for c in r["camps"]] == ["Zu kurz", "Camping Ohne Maße", "Stellplatz Nord"]
    fits = {c["name"]: c["fits"] for c in r["camps"]}
    assert fits == {"Zu kurz": "no", "Camping Ohne Maße": "unknown", "Stellplatz Nord": "ok"}
    assert r["camps"][-1]["flags"]["maxheight"] == 3.5 and r["camps"][-1]["osm"] == "node/10"
    assert r["data"] is True


def test_find_without_db_and_bad_input():
    assert navpois.find(LINE, VEH, None) == {"fuel": [], "camps": [], "data": False}
    with pytest.raises(navpois.NavPoiError):
        navpois.find([[1, 2]], VEH, None)
    with pytest.raises(navpois.NavPoiError):
        navpois.find([["x", 2], [1, 2]], VEH, None)


def test_api_only_rig(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main.osmdb, "get", lambda: FakeDB())
    rig = TestClient(main.app, base_url="https://riganalyzer.petrick.top")
    r = rig.post("/api/nav/pois", json={"shape": LINE, "vehicle": VEH}, headers=basic("bofh"))
    assert r.status_code == 200, r.text
    assert len(r.json()["fuel"]) == 2
    assert rig.post("/api/nav/pois", json={"shape": [[1, 2]]}, headers=basic("bofh")).status_code == 422
    ride = TestClient(main.app, base_url="https://rideanalyzer.petrick.top")
    assert ride.post("/api/nav/pois", json={"shape": LINE}, headers=basic("bofh")).status_code == 404
