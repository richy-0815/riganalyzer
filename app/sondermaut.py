"""Sondermaut auf der Route (RigAnalyzer, seit 0.49.0): Tunnel, Brücken, Bergstrecken mit eigenem Tarif.

Die Tabelle steht von Hand gepflegt in sondermaut.json (Punkt am Bauwerk, Art, Tarife je Klasse, Quelle).
Hier nur die Erkennung: Die Route fuehrt nah am Punkt ueber ein Bauwerk der Art (tunnel/bridge) – damit zaehlt
die Landstrasse unter dem Viadukt bzw. die Passstrasse ueber dem Tunnel nicht. Ohne Wegdaten (Live-Quelle)
reicht Autobahn/Schnellstrasse. Den Preis rechnet der Browser (toll.js) aus dem Fahrzeugprofil.
"""

import json
import math
import os

import numpy as np

_FILE = os.path.join(os.path.dirname(__file__), "sondermaut.json")
_ROADS = {"motorway", "trunk"}
_GAP_M = 5000   # zwei Durchfahrten, wenn dazwischen mehr als 5 km Route liegen
_rules = None


def rules():
    """Tabelle (sondermaut.json), einmal geladen."""
    global _rules
    if _rules is None:
        with open(_FILE, encoding="utf-8") as f:
            _rules = json.load(f)
    return _rules


def _fits(obj, i, cls, way_tags):
    tags = way_tags(i)
    if tags is None:
        return cls[i] in _ROADS
    v = tags.get(obj["art"])
    return v is not None and v != "no"


def detect(plat, plon, S, cls, way_tags):
    """-> [{"id", "km"}] je Durchfahrt, nach km sortiert. way_tags(i) -> Tags des Wegs am Punkt i oder None."""
    hits = []
    for obj in rules()["objekte"]:
        lat, lon = obj["pt"]
        r = obj.get("r", 300)
        dy = (plat - lat) * 111320.0
        dx = (plon - lon) * 111320.0 * math.cos(math.radians(lat))
        near = np.nonzero(dx * dx + dy * dy <= r * r)[0]
        last = None
        for i in near:
            if not _fits(obj, i, cls, way_tags):
                continue
            if last is None or S[i] - S[last] > _GAP_M:
                hits.append({"id": obj["id"], "km": round(float(S[i]) / 1000, 1)})
            last = i
    return sorted(hits, key=lambda h: h["km"])
