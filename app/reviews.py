"""Eigene Platz-Bewertungen (seit v0.25.0): Nutzer des RigAnalyzers bewerten Stell-/Campingplaetze nach einer Nacht –
Sterne, Text, Preis, Datum und ob ihr Fahrzeug gepasst hat (mit Massen des Fahrzeugs). Alle angemeldeten Nutzer sehen
alle Bewertungen; je Nutzer und Platz eine (neue ersetzt die alte).

Platz-Schluessel: OSM-Id („way/123“), Tourismusamt („dt:<uri>“) oder Koordinaten („ll:lat,lon“). Weil Pakete und
alte Analysen nicht immer eine Id kennen, findet die Suche einen Platz auch ueber die Lage (bis 150 m).
Ablage: DATA_DIR/reviews.sqlite"""

import json
import math
import os
import re
import sqlite3
import threading
import time

FILE = os.path.join(os.environ.get("DATA_DIR", "/data"), "reviews.sqlite")
NEAR_M = 150
FITS = ("ok", "eng", "nein")
MAX_TEXT = 800
_lock = threading.Lock()


class ReviewError(ValueError):
    """Fehler mit Text fuer die Seite."""


def _db():
    con = sqlite3.connect(FILE, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS reviews (place TEXT NOT NULL, user TEXT NOT NULL, at INTEGER NOT NULL,
        name TEXT, lat REAL NOT NULL, lon REAL NOT NULL, stars INTEGER NOT NULL, text TEXT, price REAL, night TEXT,
        fit TEXT, veh TEXT, PRIMARY KEY (place, user))""")
    con.execute("CREATE INDEX IF NOT EXISTS reviews_ll ON reviews (lat, lon)")
    return con


def _dist(la1, lo1, la2, lo2):
    k = math.pi / 180
    x = (lo2 - lo1) * k * math.cos((la1 + la2) * k / 2)
    return 6371000 * math.hypot(x, (la2 - la1) * k)


def key_ok(key):
    return bool(re.match(r"^((node|way|relation)/\d{1,15}|dt:https://data\.datatourisme\.fr/[\w/\-]{1,120}|ll:-?\d{1,2}\.\d{5},-?\d{1,3}\.\d{5})$",
                         key or ""))


def clean(body):
    """Eingabe pruefen -> dict fuer die Ablage (ReviewError bei Unsinn)."""
    key = str(body.get("key") or "")
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
        stars = int(body["stars"])
    except (KeyError, TypeError, ValueError):
        raise ReviewError("Platz und Sterne fehlen.") from None
    if not key_ok(key):
        key = f"ll:{lat:.5f},{lon:.5f}"
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not 1 <= stars <= 5:
        raise ReviewError("Sterne 1 bis 5.")
    price = body.get("price")
    if price not in (None, ""):
        try:
            price = round(float(str(price).replace(",", ".")), 2)
        except ValueError:
            raise ReviewError("Preis bitte als Zahl.") from None
        if not 0 <= price <= 300:
            raise ReviewError("Preis 0 bis 300 €.")
    else:
        price = None
    night = str(body.get("night") or "")[:10]
    if night and not re.match(r"^\d{4}-\d{2}-\d{2}$", night):
        raise ReviewError("Datum als JJJJ-MM-TT.")
    fit = body.get("fit") if body.get("fit") in FITS else None
    veh = body.get("veh") if isinstance(body.get("veh"), dict) else {}
    veh = {k: round(float(veh[k]), 2) for k in ("length", "height", "weight") if isinstance(veh.get(k), (int, float))}
    text = re.sub(r"\s+\n", "\n", str(body.get("text") or "")).strip()[:MAX_TEXT]
    return {"place": key, "name": str(body.get("name") or "")[:120], "lat": round(lat, 6), "lon": round(lon, 6),
            "stars": stars, "text": text, "price": price, "night": night or None, "fit": fit, "veh": veh}


def save(user, body):
    r = clean(body)
    with _lock, _db() as con:
        con.execute("""INSERT OR REPLACE INTO reviews (place, user, at, name, lat, lon, stars, text, price, night, fit, veh)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (r["place"], user, int(time.time()), r["name"], r["lat"], r["lon"], r["stars"], r["text"], r["price"],
                     r["night"], r["fit"], json.dumps(r["veh"])))
    return r["place"]


def delete(user, key, any_user=None):
    """Eigene Bewertung loeschen; Admins koennen mit any_user eine fremde loeschen."""
    with _lock, _db() as con:
        return con.execute("DELETE FROM reviews WHERE place = ? AND user = ?", (key, any_user or user)).rowcount


def _row(r, short=False):
    t = r["text"] or ""
    return {"user": r["user"], "at": r["at"], "stars": r["stars"], "text": (t[:160] + "…") if short and len(t) > 160 else t,
            "price": r["price"], "night": r["night"], "fit": r["fit"], "veh": json.loads(r["veh"] or "{}"),
            "place": r["place"], "name": r["name"]}


def _rows_for(con, key, lat, lon):
    rows = con.execute("SELECT * FROM reviews WHERE place = ? ORDER BY at DESC", (key,)).fetchall() if key else []
    if lat is not None:
        d = NEAR_M / 111000
        near = con.execute("SELECT * FROM reviews WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? ORDER BY at DESC",
                           (lat - d, lat + d, lon - d * 2, lon + d * 2)).fetchall()
        have = {(r["place"], r["user"]) for r in rows}
        rows += [r for r in near if (r["place"], r["user"]) not in have and _dist(lat, lon, r["lat"], r["lon"]) <= NEAR_M]
    return rows


def for_place(key, lat=None, lon=None):
    if not os.path.exists(FILE):
        return []
    with _db() as con:
        return [_row(r) for r in _rows_for(con, key, lat, lon)]


def summaries(places):
    """places: [(key, lat, lon)] -> {key: {"n", "avg", "last"}} nur fuer Plaetze mit Bewertungen."""
    if not os.path.exists(FILE):
        return {}
    out = {}
    with _db() as con:
        if not con.execute("SELECT 1 FROM reviews LIMIT 1").fetchone():
            return {}
        for key, lat, lon in places:
            rows = _rows_for(con, key, lat, lon)
            if rows:
                out[key] = {"n": len(rows), "avg": round(sum(r["stars"] for r in rows) / len(rows), 1),
                            "last": _row(rows[0], short=True)}
    return out
