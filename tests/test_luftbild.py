"""Luftbild-Kacheln ueber den eigenen Server (0.52.1): Esri sieht keine Nutzer-IP, nur gueltige Kacheln, Speicher."""
import pytest

import luftbild
import main
from test_admin import basic, env  # noqa: F401  (Fixture env)

RIG = "https://riganalyzer.petrick.top"


class _Resp:
    def __init__(self, content=b"JPG", ctype="image/jpeg", status=200):
        self.content, self.headers, self.status_code = content, {"content-type": ctype}, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise luftbild.httpx.HTTPStatusError("x", request=None, response=None)


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def get(url, **kw):
        calls.append(url)
        return _Resp()
    monkeypatch.setattr(luftbild.httpx, "get", get)
    monkeypatch.setattr(luftbild, "_tiles", {})
    monkeypatch.setattr(luftbild, "_hits", {})
    return calls


def test_tile_fetched_once_and_url_fixed(fake):
    assert luftbild.tile(16, 35210, 21490, "olaf") == (b"JPG", "image/jpeg")
    assert luftbild.tile(16, 35210, 21490, "olaf") == (b"JPG", "image/jpeg")
    assert fake == ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/16/21490/35210"]


@pytest.mark.parametrize("z,x,y", [(-1, 0, 0), (20, 0, 0), (2, 4, 0), (2, 0, 4), (2, -1, 0)])
def test_invalid_tiles_rejected(fake, z, x, y):
    with pytest.raises(ValueError):
        luftbild.tile(z, x, y, "olaf")
    assert fake == []


def test_limit_per_user(fake, monkeypatch):
    monkeypatch.setattr(luftbild, "PER_MIN", 3)
    for i in range(3):
        luftbild.tile(10, i, 0, "olaf")
    with pytest.raises(luftbild.TooMany):
        luftbild.tile(10, 9, 0, "olaf")
    luftbild.tile(10, 9, 0, "berni")          # andere Logins nicht betroffen


def test_non_image_not_passed(monkeypatch):
    monkeypatch.setattr(luftbild, "_tiles", {})
    monkeypatch.setattr(luftbild, "_hits", {})
    monkeypatch.setattr(luftbild.httpx, "get", lambda *a, **k: _Resp(b"<html>", "text/html"))
    assert luftbild.tile(3, 1, 1, "olaf") is None


def test_route_needs_login_and_serves(env, fake):  # noqa: F811 – Fixture (Import oben)
    from fastapi.testclient import TestClient
    c = TestClient(main.app, base_url=RIG)
    assert c.get("/api/luftbild/16/35210/21490").status_code == 401
    r = c.get("/api/luftbild/16/35210/21490", headers=basic("olaf"))
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == b"JPG"
    assert c.get("/api/luftbild/30/0/0", headers=basic("olaf")).status_code == 404


def test_csp_without_esri_and_meta():
    for host in ("arcgisonline", "fbcdn", "mapillary.com"):
        assert host not in main.CSP
