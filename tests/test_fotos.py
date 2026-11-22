"""Wikimedia-Fotos fuer die Detail-Karten (0.54.0, #31): nur gueltige Namen, nur Wikimedia-Hosts, nur Bilder."""
import httpx
import pytest

import fotos


@pytest.fixture(autouse=True)
def leer(monkeypatch):
    monkeypatch.setattr(fotos, "_cache", {})
    monkeypatch.setattr(fotos, "_hits", {})


def _client(responses):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return responses[str(req.url).split("?")[0]](req)
    real = httpx.Client

    def make(**kw):
        kw.pop("follow_redirects", None)
        return real(transport=httpx.MockTransport(handler), follow_redirects=False, **kw)
    return make, seen


def test_namen_pruefen():
    assert fotos.valid("Aire_de_Castellane.jpg") and fotos.valid("Burg Eltz.JPEG")
    for bad in ("../etc/passwd.jpg", "a/b.jpg", "x.svg", "x.html", "", "a#b.jpg"):
        assert not fotos.valid(bad), bad
    with pytest.raises(ValueError):
        fotos.commons("x.svg", "u")


def test_holt_vorschau_ueber_umleitung(monkeypatch):
    up = "https://upload.wikimedia.org/thumb/a.jpg"
    make, seen = _client({
        "https://commons.wikimedia.org/wiki/Special:FilePath/A_b.jpg": lambda r: httpx.Response(302, headers={"location": up}),
        up: lambda r: httpx.Response(200, content=b"\xff\xd8bild", headers={"content-type": "image/jpeg"}),
    })
    monkeypatch.setattr(fotos.httpx, "Client", make)
    assert fotos.commons("A b.jpg", "u") == (b"\xff\xd8bild", "image/jpeg")
    assert fotos.commons("A_b.jpg", "u") == (b"\xff\xd8bild", "image/jpeg")   # aus dem Speicher
    assert len(seen) == 2


def test_fremder_host_und_kein_bild(monkeypatch):
    make, _ = _client({
        "https://commons.wikimedia.org/wiki/Special:FilePath/x.jpg": lambda r: httpx.Response(302, headers={"location": "https://boese.example/x.jpg"}),
        "https://commons.wikimedia.org/wiki/Special:FilePath/y.jpg": lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}),
    })
    monkeypatch.setattr(fotos.httpx, "Client", make)
    assert fotos.commons("x.jpg", "u") is None
    assert fotos.commons("y.jpg", "u") is None


def test_bremse(monkeypatch):
    monkeypatch.setattr(fotos, "PER_MIN", 2)
    make, _ = _client({"https://commons.wikimedia.org/wiki/Special:FilePath/z.jpg": lambda r: httpx.Response(404)})
    monkeypatch.setattr(fotos.httpx, "Client", make)
    fotos.commons("z.jpg", "u")
    fotos.commons("z.jpg", "u")
    with pytest.raises(fotos.TooMany):
        fotos.commons("z.jpg", "u")
