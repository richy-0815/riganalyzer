"""Infoseite ist ohne Login erreichbar, je App eigener Text, und von der Anmeldeseite verlinkt."""
import pytest

import main


@pytest.fixture
def client(data_dir):
    from fastapi.testclient import TestClient

    def make(host):
        return TestClient(main.app, base_url=host)
    return make


@pytest.mark.parametrize("host,own,other", [
    ("https://rideanalyzer.petrick.top", "Motorradtour", "Gespann"),
    ("https://riganalyzer.petrick.top", "Gespann", "Motorradtour"),
])
def test_info_page_public_per_app(client, host, own, other):
    c = client(host)
    r = c.get("/info.html", follow_redirects=False)
    assert r.status_code == 200
    assert own in r.text and other not in r.text
    assert 'href="/login"' in r.text
    assert 'href="/info.html"' in c.get("/login").text
