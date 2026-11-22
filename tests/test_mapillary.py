"""Einfahrt-Foto (Mapillary, seit v0.25.0): naechstes Bild mit Blick zum Platz, ohne Schluessel nichts."""
import mapillary

PLACE = (43.7600, 6.1500)


def img(i, lat, lon, ang, thumb=True):
    return {"id": str(i), "thumb_1024_url": "https://x/t.jpg" if thumb else None, "captured_at": 1720000000000,
            "computed_compass_angle": ang, "computed_geometry": {"coordinates": [lon, lat]}}


def test_pick_prefers_looking_at_place():
    south = (43.7597, 6.1500)                                    # ~33 m suedlich, Platz liegt im Norden (0 Grad)
    ims = [img(1, *south, 180), img(2, *south, 5), img(3, 43.7606, 6.1500, 270), img(4, 43.7700, 6.15, 0)]
    best = mapillary.pick(ims, *PLACE)
    assert best["id"] == "2" and best["looks"] is True and best["dist"] == 33
    assert best["link"].endswith("pKey=2&focus=photo") and best["at"] == 1720000000


def test_pick_nothing_near():
    assert mapillary.pick([img(4, 43.7700, 6.15, 0), img(5, *PLACE, 0, thumb=False)], *PLACE) is None


def test_disabled_without_token(monkeypatch):
    monkeypatch.setattr(mapillary, "TOKEN", "")
    assert mapillary.photo(*PLACE) == {"enabled": False, "photo": None}


class _Resp:
    def __init__(self, data=None, content=b"", ctype="image/jpeg"):
        self._data, self.content, self.headers = data, content, {"content-type": ctype}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_photo_goes_through_own_server(monkeypatch):
    """0.52.1: der Browser bekommt nur /api/camps/photo/<id> – das Bild holt der Server, Meta sieht keine Nutzer-IP."""
    monkeypatch.setattr(mapillary, "TOKEN", "t")
    monkeypatch.setattr(mapillary, "_cache", {})
    monkeypatch.setattr(mapillary, "_thumbs", {})
    monkeypatch.setattr(mapillary, "_imgs", {})
    data = {"data": [{"id": "123", "thumb_1024_url": "https://scontent-a.xx.fbcdn.net/t.jpg?x=1", "captured_at": 1,
                      "computed_geometry": {"coordinates": [PLACE[1], PLACE[0] - 0.0002]}, "compass_angle": 0}]}
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _Resp(data) if "graph.mapillary.com" in url else _Resp(content=b"JPEG")
    monkeypatch.setattr(mapillary.httpx, "get", fake_get)
    ph = mapillary.photo(*PLACE)["photo"]
    assert ph["thumb"] == "/api/camps/photo/123" and "fbcdn" not in str(ph)
    assert mapillary.image("123") == (b"JPEG", "image/jpeg")
    assert mapillary.image("123") == (b"JPEG", "image/jpeg")          # zweites Mal aus dem Speicher
    assert sum("fbcdn" in c for c in calls) == 1
    assert mapillary.image("999") is None                              # nie vorgeschlagen -> nichts holen


def test_image_only_from_mapillary_hosts(monkeypatch):
    monkeypatch.setattr(mapillary, "_imgs", {})
    monkeypatch.setattr(mapillary, "_thumbs", {"1": (9e12, "https://evil.example/x.jpg"), "2": (9e12, "http://x.fbcdn.net/a"),
                                               "3": (9e12, "https://x.fbcdn.net.evil.example/a")})
    monkeypatch.setattr(mapillary.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein Abruf")))
    for i in ("1", "2", "3"):
        assert mapillary.image(i) is None


def test_image_rejects_non_images(monkeypatch):
    monkeypatch.setattr(mapillary, "_imgs", {})
    monkeypatch.setattr(mapillary, "_thumbs", {"5": (9e12, "https://a.fbcdn.net/x")})
    monkeypatch.setattr(mapillary.httpx, "get", lambda *a, **k: _Resp(content=b"<html>", ctype="text/html"))
    assert mapillary.image("5") is None
