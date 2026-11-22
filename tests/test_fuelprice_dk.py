"""Daenemark echt (0.32.0): OK-Pflicht-API, DKK -> EUR, Diesel ohne HVO, Zuordnung <= 150 m."""
import fuelprice_dk as dk
import fuelprice_nordic as fn

OK = {"items": [
    {"facility_number": 1, "street": "Vejlevej", "city": "Kolding", "coordinates": {"latitude": 55.49, "longitude": 9.47},
     "last_updated_time": "2026-11-02T08:30:00Z",
     "prices": [{"product_name": "Blyfri 95", "price": 14.1}, {"product_name": "Svovlfri Diesel", "price": 13.49},
                {"product_name": "HVO Diesel", "price": 15.2}]},
    {"facility_number": 2, "coordinates": {"latitude": 56.0, "longitude": 10.0}, "prices": [{"product_name": "Blyfri 95", "price": 14}]},
    {"facility_number": 3, "coordinates": {}, "prices": [{"product_name": "Diesel", "price": 13}]},
]}


def test_parse_ok():
    st = dk.parse_ok(OK, 7.46)
    assert len(st) == 1 and st[0]["diesel"] == round(13.49 / 7.46, 3) and st[0]["name"] == "OK Vejlevej Kolding"
    assert st[0]["at"] > 0


def test_lookup_matches_and_skips_outside(monkeypatch):
    monkeypatch.setattr(dk, "_state", {"t": 0.0, "grid": None, "paused": 0.0, "loading": False})
    monkeypatch.setattr(dk, "BACKGROUND", False)
    monkeypatch.setattr(fn, "fx", lambda: {"DKK": 7.46})

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return OK
    monkeypatch.setattr(dk.httpx, "get", lambda *a, **k: R())
    out, pending = dk.lookup([("a", 55.4905, 9.4702), ("b", 55.6, 9.6), ("c", 48.1, 11.6)])
    assert set(out) == {"a"} and out["a"]["cc"] == "DK" and out["a"]["src"] == "OK" and not pending


def test_ketten_list_documents_status():
    assert dk.KETTEN["OK"]["status"] == "eingebaut"
    assert all({"status", "url", "doc", "note"} <= set(v) for v in dk.KETTEN.values())
