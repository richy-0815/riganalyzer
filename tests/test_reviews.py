"""Eigene Platz-Bewertungen (seit v0.25.0)."""
import pytest

import reviews
from test_admin import basic, env  # noqa: F401  (Fixture env)


@pytest.fixture(autouse=True)
def tmpfile(tmp_path, monkeypatch):
    monkeypatch.setattr(reviews, "FILE", str(tmp_path / "reviews.sqlite"))


def test_one_per_user_and_place_and_summary():
    reviews.save("olaf", {"key": "way/1", "lat": 43.76, "lon": 6.15, "stars": 3, "text": "laut"})
    reviews.save("olaf", {"key": "way/1", "lat": 43.76, "lon": 6.15, "stars": 4, "text": "doch ok", "price": "18,50",
                         "night": "2026-07-03", "fit": "eng", "veh": {"length": 7.43, "height": 2.9, "x": 1}})
    reviews.save("eva", {"key": "way/1", "lat": 43.76, "lon": 6.15, "stars": 2})
    rows = reviews.for_place("way/1")
    assert len(rows) == 2
    mine = next(r for r in rows if r["user"] == "olaf")
    assert mine["stars"] == 4 and mine["price"] == 18.5 and mine["fit"] == "eng" and mine["veh"] == {"length": 7.43, "height": 2.9}
    s = reviews.summaries([("way/1", 43.76, 6.15), ("way/9", 50.0, 8.0)])
    assert s["way/1"]["n"] == 2 and s["way/1"]["avg"] == 3.0 and "way/9" not in s


def test_found_by_position_without_id():
    """Pakete/alte Analysen ohne OSM-Id: Platz ueber die Lage finden (bis 150 m)."""
    reviews.save("olaf", {"key": "way/1", "lat": 43.76, "lon": 6.15, "stars": 5})
    assert reviews.summaries([("ll:43.76050,6.15050", 43.7605, 6.1505)])["ll:43.76050,6.15050"]["n"] == 1
    assert reviews.summaries([("x", 43.77, 6.15)]) == {}                      # ~1,1 km weg


def test_validation():
    with pytest.raises(reviews.ReviewError):
        reviews.save("olaf", {"key": "way/1", "lat": 1, "lon": 2, "stars": 7})
    with pytest.raises(reviews.ReviewError):
        reviews.save("olaf", {"key": "way/1", "lat": 1, "lon": 2, "stars": 3, "night": "gestern"})
    key = reviews.save("olaf", {"key": "<script>", "lat": 43.1, "lon": 6.2, "stars": 3, "text": "x" * 2000})
    assert key == "ll:43.10000,6.20000" and len(reviews.for_place(key)[0]["text"]) == reviews.MAX_TEXT


def test_api_save_list_delete(env):  # noqa: F811 – Fixture (Import oben)
    h = basic("olaf")
    r = env.post("/api/reviews", json={"key": "node/5", "lat": 45.0, "lon": 5.0, "stars": 4, "text": "schön"}, headers=h)
    assert r.status_code == 200 and r.json()["key"] == "node/5"
    j = env.get("/api/reviews", params={"key": "node/5"}, headers=h).json()
    assert j["mine"]["stars"] == 4 and j["me"] == "olaf"
    assert env.post("/api/reviews/lookup", json={"places": [["node/5", 45.0, 5.0]]}, headers=h).json()["places"]["node/5"]["n"] == 1
    assert env.post("/api/reviews", json={"key": "node/5", "lat": 45, "lon": 5, "stars": 9}, headers=h).status_code == 422
    assert env.delete("/api/reviews", params={"key": "node/5", "user": "bofh"}, headers=h).status_code == 403
    assert env.delete("/api/reviews", params={"key": "node/5"}, headers=h).json()["deleted"] == 1
    assert env.get("/api/reviews", params={"key": "node/5"}, headers={"user-agent": "x"}).status_code == 401
