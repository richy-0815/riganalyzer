"""Sondermaut (Tunnel/Brücken, seit 0.49.0): Tabelle + Erkennung auf der Route."""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import sondermaut
from test_admin import basic, env  # noqa: F401  (Fixture env)

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def _rules():
    with open(os.path.join(APP, "sondermaut.json"), encoding="utf-8") as f:
        return json.load(f)


def test_schema():
    r = _rules()
    assert r["version"] == 1 and r["stand"]
    ids = [o["id"] for o in r["objekte"]]
    assert len(ids) == len(set(ids))
    for o in r["objekte"]:
        assert o["schema"] in ("FR", "hoehe", "womo", "at"), o["id"]
        assert o["art"] in ("tunnel", "bridge"), o["id"]
        assert o["waehrung"] in ("EUR", "CHF") and o["quelle"].startswith("https://") and o["geprueft"], o["id"]
        lat, lon = o["pt"]
        assert 35 < lat < 60 and -10 < lon < 30, o["id"]
        assert o["preise"] and all(isinstance(v, (int, float)) and 0 < v < 500 for v in o["preise"].values()), o["id"]
    assert {"millau", "montblanc", "frejus", "gsb", "brenner"} <= set(ids)


def _line(lat0, lon0, lat1, lon1, n=400):
    """Gerade Route mit n Punkten im 10-m-Raster (S in m)."""
    plat, plon = np.linspace(lat0, lat1, n), np.linspace(lon0, lon1, n)
    return plat, plon, np.arange(n) * 10.0


def test_detect_millau_bridge():
    # Nord-Süd über den Viadukt (44.0799, 3.0224)
    plat, plon, S = _line(44.095, 3.0224, 44.065, 3.0224)
    cls = np.array(["motorway"] * len(plat), dtype=object)
    tags = lambda i: {"bridge": "viaduct", "toll": "yes"}  # noqa: E731
    hits = sondermaut.detect(plat, plon, S, cls, tags)
    assert [h["id"] for h in hits] == ["millau"]
    assert 1.0 < hits[0]["km"] < 3.0


def test_detect_needs_structure():
    # gleiche Linie, aber unten im Tal auf der Landstrasse (keine Bruecke) -> kein Treffer
    plat, plon, S = _line(44.095, 3.0224, 44.065, 3.0224)
    cls = np.array(["primary"] * len(plat), dtype=object)
    assert sondermaut.detect(plat, plon, S, cls, lambda i: {"highway": "primary"}) == []


def test_detect_without_way_tags_uses_road_class():
    plat, plon, S = _line(44.095, 3.0224, 44.065, 3.0224)
    assert [h["id"] for h in sondermaut.detect(plat, plon, S, np.array(["motorway"] * len(plat), dtype=object), lambda i: None)] == ["millau"]
    assert sondermaut.detect(plat, plon, S, np.array(["secondary"] * len(plat), dtype=object), lambda i: None) == []


def test_detect_far_away():
    plat, plon, S = _line(52.5, 13.3, 52.6, 13.4)
    cls = np.array(["motorway"] * len(plat), dtype=object)
    assert sondermaut.detect(plat, plon, S, cls, lambda i: {"tunnel": "yes"}) == []


def test_detect_twice_on_round_trip():
    # hin über die Brücke, 20 km weg (hier: weit nördlich), zurück über die Brücke
    a = _line(44.095, 3.0224, 44.065, 3.0224, 400)
    b = _line(44.065, 3.0224, 44.25, 3.0224, 2100)
    c = _line(44.25, 3.0224, 44.065, 3.0224, 2100)
    plat = np.concatenate([a[0], b[0], c[0]])
    plon = np.concatenate([a[1], b[1], c[1]])
    S = np.arange(len(plat)) * 10.0
    hits = sondermaut.detect(plat, plon, S, np.array(["motorway"] * len(plat), dtype=object), lambda i: {"bridge": "yes"})
    assert [h["id"] for h in hits] == ["millau", "millau"]


def test_api_requires_login(env):  # noqa: F811 – Fixture (Import oben)
    assert env.get("/api/sondermaut", headers={"user-agent": "okhttp/5"}).status_code == 401
    r = env.get("/api/sondermaut", headers=basic("olaf"))
    assert r.status_code == 200 and r.json() == _rules()
