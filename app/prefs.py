"""Vorlieben am Login (seit v0.29.0): Fahrzeugprofile, Karten-Zoom, offener Reiter und Kartenebenen.

Webseite und RigNavi lesen dieselbe Ablage, damit ein Profil wie „uschi+anhänger“ und das Zoom-Tempo
ueberall gleich sind. Ablage: data/prefs/<benutzer>.json – ein kleines JSON, je Bereich mit Zeitstempel `at`
(Millisekunden), damit bei zwei Geraeten der neuere Stand gewinnt.
"""

import json
import os
import re
import threading

MAX_BYTES = 64_000
_lock = threading.Lock()

ZOOM_WHEEL = ("normal", "schnell", "sehr_schnell")
LAYERS = ("issues", "stops", "fuel", "camp", "dump", "water", "steep", "lez", "radar")
TABS = ("issues", "camp", "fuel", "rest", "steep", "fuelinfo")
MAX_PROFILES = 8
KONTO_CARDS = ("touren", "zugang", "mail", "daten", "rignavi")
ADMIN_CARDS = ("logins", "aktiv", "rueckmeldungen", "gruppen", "kosten", "newsletter", "aenderungen", "versionen")
ADMIN_KEYS = {"order", "collapsed", "wide", "col", "at"}
# Karten der Ride-Hauptseite (dock.js): eigener Bereich „ride_ui“ – „ui“ gehoert Rig, Bereiche gelten je Login
RIDE_CARDS = ("upload", "planner", "result", "profiles", "highlights", "weather", "list", "quality", "compare", "band")
RIDE_UI_KEYS = {"left", "right", "collapsed", "at"}
MAIL_KEYS = {"releases", "newsletter", "asked"}


class PrefsError(ValueError):
    """Fehler mit Text fuer die Seite."""


def _path(user):
    name = re.sub(r"[^a-z0-9._-]", "_", user.strip().lower())
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "prefs", name + ".json")


def get(user):
    try:
        with open(_path(user), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _at(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 1e14


def _check(part, data):
    """Grobe Pruefung je Bereich – die Werte selbst prueft die Seite/App beim Uebernehmen."""
    if not isinstance(data, dict) or not _at(data.get("at")):
        raise PrefsError(f"{part}: Zeitstempel fehlt.")
    if part == "zoom":
        if data.get("wheel") not in ZOOM_WHEEL:
            raise PrefsError("Zoom-Tempo unbekannt.")
        j = data.get("jump")
        if not isinstance(j, int) or isinstance(j, bool) or not 12 <= j <= 18:
            raise PrefsError("Zielzoom 12 bis 18.")
    elif part == "profiles":
        prof = data.get("profiles")
        if not isinstance(prof, dict) or not 1 <= len(prof) <= MAX_PROFILES or data.get("active") not in prof:
            raise PrefsError("Fahrzeugprofile ungültig.")
        for name, p in prof.items():
            if not isinstance(name, str) or not 1 <= len(name) <= 30 or not isinstance(p, dict):
                raise PrefsError("Fahrzeugprofil ungültig.")
    elif part == "ui":
        if data.get("tab") is not None and data.get("tab") not in TABS:
            raise PrefsError("Reiter unbekannt.")
        lay = data.get("layers")
        if lay is not None and (not isinstance(lay, list) or any(x not in LAYERS for x in lay)):
            raise PrefsError("Kartenebene unbekannt.")
    elif part == "konto":
        for k in ("order", "collapsed"):
            v = data.get(k)
            if v is not None and (not isinstance(v, list) or len(v) > len(KONTO_CARDS) or any(x not in KONTO_CARDS for x in v)):
                raise PrefsError("Kachel unbekannt.")
    elif part == "admin":   # Admin-Dashboard: streng, nur bekannte Kacheln, je Kachel hoechstens einmal
        if set(data) - ADMIN_KEYS:
            raise PrefsError("Admin-Anordnung: unbekannter Eintrag.")
        for k in ("order", "collapsed", "wide"):
            v = data.get(k)
            if v is not None and (not isinstance(v, list) or len(v) > len(ADMIN_CARDS) or len(set(map(str, v))) != len(v)
                                  or any(x not in ADMIN_CARDS for x in v)):
                raise PrefsError("Kachel unbekannt.")
        col = data.get("col")
        if col is not None and (not isinstance(col, dict)
                                or any(k not in ADMIN_CARDS or v not in ("l", "r") for k, v in col.items())):
            raise PrefsError("Spalte unbekannt.")
    elif part == "ride_ui":   # Ride-Hauptseite: streng, nur bekannte Karten, je Karte hoechstens einmal
        if set(data) - RIDE_UI_KEYS:
            raise PrefsError("Karten-Anordnung: unbekannter Eintrag.")
        placed = []
        for k in ("left", "right", "collapsed"):
            v = data.get(k)
            if v is None:
                continue
            if (not isinstance(v, list) or len(v) > len(RIDE_CARDS) or any(x not in RIDE_CARDS for x in v)
                    or len(set(v)) != len(v)):
                raise PrefsError("Karte unbekannt.")
            if k != "collapsed":
                placed += v
        if len(set(placed)) != len(placed):
            raise PrefsError("Karte doppelt.")
    elif part == "mail":   # waehlbare Mails: nur bekannte Schalter, nur ja/nein
        if set(data) - MAIL_KEYS - {"at"} or any(not isinstance(data[k], bool) for k in MAIL_KEYS if k in data):
            raise PrefsError("Mail-Einstellung ungültig.")
    else:
        raise PrefsError(f"Unbekannter Bereich {part}.")


def put(user, changes):
    """changes: {bereich: {..., at}} – je Bereich gewinnt der neuere Zeitstempel. -> kompletter Stand."""
    if not isinstance(changes, dict) or not changes:
        raise PrefsError("Nichts zu speichern.")
    for part, data in changes.items():
        _check(part, data)
    with _lock:
        cur = get(user)
        for part, data in changes.items():
            if (cur.get(part) or {}).get("at", 0) <= data["at"]:
                cur[part] = data
        raw = json.dumps(cur, ensure_ascii=False)
        if len(raw.encode()) > MAX_BYTES:
            raise PrefsError("Zu viele Einstellungen.")
        path = _path(user)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(path + ".tmp", path)
    return cur
