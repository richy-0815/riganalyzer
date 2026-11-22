"""Echte Dieselpreise (Tankerkönig, seit v0.17.0): nur DE, gebuendelt, OSM -> naechste TK-Station <= 150 m, Cache."""
import pytest

import fuelprice
from test_admin import basic, env  # noqa: F401  (Fixture env)


class Fake:
    def __init__(self, stations):
        self.stations, self.calls, self.prices = stations, [], {}

    def __call__(self, url, params=None, **kw):
        self.calls.append(params)
        if "ids" in params:   # prices.php
            data = {"ok": True, "prices": {i: self.prices.get(i, {"status": "no prices"}) for i in params["ids"].split(",")}}
        else:
            data = {"ok": True, "stations": [dict(s) for s in self.stations
                                               if abs(s["lat"] - params["lat"]) < 0.06 and abs(s["lng"] - params["lng"]) < 0.09]}

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return data
        return R()


TK = [{"id": "a", "name": "ARAL", "lat": 52.5001, "lng": 13.4001, "diesel": 1.719, "isOpen": True},
      {"id": "b", "name": "JET", "lat": 52.5205, "lng": 13.4405, "diesel": 1.659, "isOpen": False},
      {"id": "c", "name": "Fern", "lat": 52.6, "lng": 13.6, "diesel": 1.5, "isOpen": True}]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    fuelprice._cache.clear()
    fuelprice._state.update(last=0.0, paused_until=0.0)
    monkeypatch.setattr(fuelprice, "MIN_GAP", 0)
    monkeypatch.setattr(fuelprice, "KEY", "test-key")
    fake = Fake(TK)
    monkeypatch.setattr(fuelprice.httpx, "get", fake)
    return fake


def test_matches_nearest_within_150m_and_bundles(setup):
    st = [("way/1", 52.5000, 13.4000), ("node/2", 52.5200, 13.4400), ("node/3", 52.5100, 13.4200)]   # 3: keine TK-Station nah
    res = fuelprice.prices(st)
    assert res["way/1"]["diesel"] == 1.719 and res["way/1"]["open"] is True and res["way/1"]["name"] == "ARAL"
    assert res["node/2"]["diesel"] == 1.659 and res["node/2"]["open"] is False
    assert "node/3" not in res
    assert len(setup.calls) == 1 and setup.calls[0]["type"] == "diesel" and setup.calls[0]["apikey"] == "test-key"


def test_cache_and_only_germany(setup):
    st = [("way/1", 52.5000, 13.4000)]
    fuelprice.prices(st)
    fuelprice.prices(st)
    assert len(setup.calls) == 1
    assert fuelprice.prices([("node/9", 43.85, 6.51)]) == {}           # Verdon: kein Abruf
    assert len(setup.calls) == 1


def test_no_key_no_calls(setup, monkeypatch):
    monkeypatch.setattr(fuelprice, "KEY", "")
    assert fuelprice.prices([("way/1", 52.5, 13.4)]) == {} and setup.calls == []


def test_call_cap(setup):
    st = [(f"n/{i}", 47.5 + i * 0.25, 9.0) for i in range(60)]          # 60 weit auseinander
    fuelprice.prices(st)
    assert len(setup.calls) == fuelprice.MAX_CALLS


def test_api(env):  # noqa: F811 – Fixture (Import oben)
    body = {"stations": [["way/1", 52.5, 13.4]]}
    r = env.post("/api/fuel/prices", json=body, headers=basic("olaf"))
    assert r.status_code == 200 and r.json()["prices"]["way/1"]["diesel"] == 1.719
    assert "Tankerkönig" in r.json()["source"]
    assert env.post("/api/fuel/prices", json=body, headers={"user-agent": "okhttp/5"}).status_code == 401
    assert env.post("/api/fuel/prices", json={"stations": [["x", 1, 2]] * 401}, headers=basic("olaf")).status_code == 422


def test_max_calls_is_small():
    assert fuelprice.MAX_CALLS <= 8


def test_pause_after_error(setup, monkeypatch):
    """Sperre/Fehler bei Tankerkönig: 15 min gar nicht mehr anklopfen."""
    def boom(url, params=None, **kw):
        setup.calls.append(params)
        raise fuelprice.httpx.ConnectError("gesperrt")
    monkeypatch.setattr(fuelprice.httpx, "get", boom)
    st = [(f"n/{i}", 48.0 + i * 0.25, 9.0) for i in range(5)]
    assert fuelprice.prices(st) == {}
    assert len(setup.calls) == 1                                      # nach dem ersten Fehler Schluss
    assert fuelprice.prices(st) == {} and len(setup.calls) == 1       # auch die naechste Anfrage nicht
    assert fuelprice._state["paused_until"] - fuelprice.time.time() > 14 * 60


def test_gap_between_calls(setup, monkeypatch):
    monkeypatch.setattr(fuelprice, "MIN_GAP", 1.5)
    slept = []
    monkeypatch.setattr(fuelprice.time, "sleep", lambda s: slept.append(s))
    fuelprice.prices([("a", 52.5, 13.4), ("b", 50.0, 8.0)])
    assert len(setup.calls) == 2 and slept and max(slept) > 1.0


def test_type_diesel_answers_with_price_field(setup):
    """Tankerkönig liefert bei type=diesel das Feld „price“ statt „diesel“ (gefunden auf QA, 31.10.2026)."""
    setup.stations = [{"id": "p", "name": "BFT Paluza", "lat": 49.1417, "lng": 8.5530, "price": 1.739, "isOpen": False},
                         {"id": "g", "name": "Globus", "lat": 50.5650, "lng": 8.6062, "price": 1.689, "isOpen": True},
                         {"id": "x", "name": "zu", "lat": 50.5000, "lng": 8.6000, "price": None, "isOpen": False}]
    res = fuelprice.prices([("bft", 49.1414766, 8.5529372), ("globus", 50.5618308, 8.6062108)])
    assert res["bft"]["diesel"] == 1.739 and res["bft"]["open"] is False
    assert res["globus"]["diesel"] == 1.689          # ~350 m, aber eindeutig


def test_far_match_only_if_unambiguous():
    tk = [{"lat": 50.0030, "lng": 8.0, "price": 1.7}, {"lat": 50.0034, "lng": 8.0, "price": 1.8}]
    assert fuelprice._match(tk, 50.0, 8.0) is None    # beide ~350 m: lieber schaetzen
    assert fuelprice._match(tk[:1], 50.0, 8.0)["price"] == 1.7


def test_stations_kept_prices_refreshed_in_batches(setup, monkeypatch):
    """0.23.0: Gebiet (list.php) 24 h gemerkt, Preise danach per prices.php – ein Abruf fuer mehrere Stationen."""
    st = [("way/1", 52.5000, 13.4000), ("node/2", 52.5200, 13.4400)]
    fuelprice.prices(st)
    assert len(setup.calls) == 1 and "lat" in setup.calls[0]
    setup.prices = {"a": {"status": "open", "diesel": 1.689}, "b": {"status": "closed"}}
    t0 = fuelprice.time.time()
    monkeypatch.setattr(fuelprice.time, "time", lambda: t0 + 700)         # Preise aelter als 10 min
    res = fuelprice.prices(st)
    assert len(setup.calls) == 2 and setup.calls[1]["ids"] in ("a,b", "b,a")   # kein neues Gebiet, ein Preisabruf
    assert res["way/1"]["diesel"] == 1.689 and res["way/1"]["open"] is True
    assert res["node/2"]["diesel"] == 1.659 and res["node/2"]["open"] is False  # geschlossen: letzter Preis bleibt
    fuelprice.prices(st)
    assert len(setup.calls) == 2                                           # jetzt wieder frisch
