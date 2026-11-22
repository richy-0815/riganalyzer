"""Ortssuche (seit v0.10.1): ganz Europa finden, Planungsgebiet bevorzugt, Treffer ausserhalb gekennzeichnet."""
import planner


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def _hit(name, lat, lon, cc):
    return {"display_name": name, "lat": str(lat), "lon": str(lon), "address": {"country_code": cc}}


def test_finds_poland_marks_outside(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kw):
        seen.update(params)
        return FakeResp([_hit("Castellane, France", 43.85, 6.51, "fr"), _hit("Kołobrzeg, Polska", 54.18, 15.58, "pl")])

    monkeypatch.setattr(planner.httpx, "get", fake_get)
    planner._geo_cache.clear()
    res = planner.geocode("Kolberg Hafen")
    assert [r["in_area"] for r in res] == [True, False]
    assert res[1]["name"].startswith("Kołobrzeg")
    assert "countrycodes" not in seen               # nicht mehr auf das Planungsgebiet beschraenkt
    assert seen.get("viewbox") and seen.get("bounded") in (None, 0, "0")   # Gebiet nur bevorzugt
    assert seen.get("addressdetails") in (1, "1")
