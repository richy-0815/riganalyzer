"""OSM-Kacheln brauchen einen Referer (Tile Usage Policy) – sonst 403 „Access blocked“ (Admin-Karte, 27.10.2026)."""
import glob
import os
import re

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def test_every_osm_tile_layer_sends_referrer():
    bad = []
    for f in glob.glob(os.path.join(APP, "static*", "*.js")):
        src = open(f, encoding="utf-8").read()
        for m in re.finditer(r'L\.tileLayer\(\s*"https://tile\.openstreetmap\.org[^"]*"\s*,\s*\{([^}]*)\}', src):
            if "referrerPolicy" not in m.group(1):
                bad.append(f"{os.path.basename(f)}:{src[:m.start()].count(chr(10)) + 1}")
    assert not bad, bad
