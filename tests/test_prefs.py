"""Vorlieben am Login (0.29.0): Bereiche mit Zeitstempel, neuerer gewinnt, Pruefung, nur eigene."""
from test_api_trips import basic, client  # noqa: F401  (Fixture client)

PROF = {"active": "uschi+anhänger", "profiles": {"uschi+anhänger": {"height": 2.9, "length": 11.89, "trailer": True}}}


def test_roundtrip_and_newer_wins(client):  # noqa: F811 – Fixture (Import oben)
    assert client.get("/api/me/prefs", headers=basic("richy")).json() == {}
    r = client.put("/api/me/prefs", json={"profiles": {**PROF, "at": 2000}, "zoom": {"wheel": "sehr_schnell", "jump": 17, "at": 2000}},
                   headers=basic("richy"))
    assert r.status_code == 200, r.text
    # aelterer Stand eines anderen Geraets ueberschreibt nicht
    client.put("/api/me/prefs", json={"zoom": {"wheel": "normal", "jump": 14, "at": 1000}}, headers=basic("richy"))
    d = client.get("/api/me/prefs", headers=basic("richy")).json()
    assert d["zoom"]["wheel"] == "sehr_schnell" and d["profiles"]["active"] == "uschi+anhänger"
    client.put("/api/me/prefs", json={"ui": {"tab": "steep", "layers": ["fuel", "camp"], "at": 3000}}, headers=basic("richy"))
    assert client.get("/api/me/prefs", headers=basic("richy")).json()["ui"]["tab"] == "steep"
    assert client.get("/api/me/prefs", headers=basic("olaf")).json() == {}      # jeder hat seine eigenen


def test_rejects_bad_input(client):  # noqa: F811 – Fixture (Import oben)
    bad = [{"zoom": {"wheel": "turbo", "jump": 16, "at": 1}}, {"zoom": {"wheel": "normal", "jump": 30, "at": 1}},
           {"profiles": {"active": "x", "profiles": {}, "at": 1}}, {"ui": {"layers": ["boese"], "at": 1}},
           {"zoom": {"wheel": "normal", "jump": 16}}, {"sonstwas": {"at": 1}}, {}]
    for b in bad:
        assert client.put("/api/me/prefs", json=b, headers=basic("richy")).status_code == 422, b
    assert client.get("/api/me/prefs").status_code == 401
