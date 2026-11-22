"""Analyse zur Tour speichern (0.28.1): ablegen, laden, Kennzeichen in der Liste, mit der Tour loeschen, nur eigene."""
from test_api_trips import STOPS, basic, client  # noqa: F401  (Fixture client)

H = "0123456789abcdef0123456789abcdef"
RES = {"trip": True, "stages": [{"stats": {"km": 12.5}, "segments": []}], "missing": []}


def _tour(client):  # noqa: F811 – Fixture (Import oben)
    r = client.post("/api/tours", json={"name": "Test", "stops": STOPS, "plan": {"v": 1, "pts": STOPS}}, headers=basic("richy"))
    assert r.status_code == 200, r.text
    return r.json()["trip"]["id"]


def test_save_load_list_delete(client):  # noqa: F811 – Fixture (Import oben)
    tid = _tour(client)
    r = client.get(f"/api/trips/{tid}/analysis", headers=basic("richy"))   # eigene Tour ohne Analyse: null statt 404
    assert r.status_code == 200 and r.json() is None
    r = client.put(f"/api/trips/{tid}/analysis", json={"gpx_hash": H, "result": RES}, headers=basic("richy"))
    assert r.status_code == 200 and r.json()["saved"]
    a = client.get(f"/api/trips/{tid}/analysis", headers=basic("richy")).json()
    assert a["gpx_hash"] == H and a["result"] == RES and a["saved"]
    lst = client.get("/api/trips", headers=basic("richy")).json()
    assert len(lst) == 1 and lst[0]["analysis"]                 # Analyse-Datei zaehlt nicht als eigene Tour
    assert client.delete(f"/api/trips/{tid}", headers=basic("richy")).status_code == 204
    assert client.get(f"/api/trips/{tid}/analysis", headers=basic("richy")).status_code == 404


def test_only_own_and_valid(client):  # noqa: F811 – Fixture (Import oben)
    tid = _tour(client)
    assert client.put(f"/api/trips/{tid}/analysis", json={"gpx_hash": H, "result": RES}, headers=basic("olaf")).status_code == 404
    assert client.get(f"/api/trips/{tid}/analysis", headers=basic("olaf")).status_code == 404
    assert client.put(f"/api/trips/{tid}/analysis", json={"gpx_hash": "kaputt", "result": RES}, headers=basic("richy")).status_code == 422
    assert client.put(f"/api/trips/{tid}/analysis", json={"gpx_hash": H, "result": []}, headers=basic("richy")).status_code == 422
