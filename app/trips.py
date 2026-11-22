"""Reisen fuer RigNavi (seit v0.9): die Webseite schickt eine geplante Reise "ans Navi",
Der Server baut daraus ein Offline-Paket (package.py), die App laedt es.

Eine Reise beschreibt nur, WAS gefahren wird (Name, Fahrzeug, Ziele mit Uebernachtungen) –
wer sie anlegt (heute Webseite, spaeter App), ist egal.
Ablage: data/trips/<benutzer>/<id>.json
"""

import gzip
import json
import os
import re
import secrets
import time

from planner import MAX_POINTS, _num

ID_RE = r"^[0-9a-f]{12}$"
MAX_NAME = 80
MODES = ("fast", "noway")
MAX_PLAN = 200_000       # Bytes: kompletter Planer-Stand einer Tour (Punkte, Plaetze, Einstellungen)
MAX_NOTE = 4000
MAX_TRIPS = 200               # Touren je Login (sonst laeuft die Platte voll)
MAX_USER_BYTES = 1_500_000_000   # Platz je Login fuer Touren, Versionen und gespeicherte Analysen
MAX_ANALYSIS = 40_000_000   # Bytes JSON (ungepackt) einer gespeicherten Analyse – gzip macht ~1/10 daraus
KEEP_VERSIONS = 10   # wie im Web-Planer: Autobahn erlaubt / meiden

# wie planner._rig_costing (+ Tank/Verbrauch fuer die Reichweite in der App)
VEHICLE = {"height": (1.5, 5, 3.2), "width": (1, 4, 2.35), "length": (3, 25, 7.5), "weight": (1, 40, 3.5),
           "axle": (0.5, 12, 2.5), "vmax": (40, 130, 100), "tank": (20, 300, 90), "consumption": (5, 40, 12),
           "zgg": (1, 12, 3.5)}   # zul. Gesamtgewicht des Zugfahrzeugs (t) fuer die franzoesische Fahrzeugklasse


class TripError(ValueError):
    """Fehler mit Text fuer die Seite."""


def _dir(user):
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "trips", user.strip().lower())


def _path(user, trip_id):
    if not isinstance(trip_id, str) or not re.match(ID_RE, trip_id):
        return None
    return os.path.join(_dir(user), trip_id + ".json")


def _vehicle(v):
    v = dict(v) if isinstance(v, dict) else {}
    if "consumption" not in v and "cons" in v:   # Webseite nennt den Verbrauch "cons"
        v["consumption"] = v["cons"]
    out = {k: _num(v.get(k), lo, hi, d) for k, (lo, hi, d) in VEHICLE.items()}
    out["trailer"] = bool(v.get("trailer"))
    out["tempo100"] = bool(v.get("tempo100"))   # DE: Gespann mit Tempo-100-Zulassung
    return out


PAUSES = ("auto", "2h20", "none")   # wie static-rig/aufenthalt.js PAUSE


def _stops(stops):
    if not isinstance(stops, list) or not 2 <= len(stops) <= MAX_POINTS:
        raise TripError(f"Eine Reise braucht 2 bis {MAX_POINTS} Ziele.")
    out = []
    for s in stops:
        try:
            lat, lon = float(s["lat"]), float(s["lon"])
        except (KeyError, TypeError, ValueError):
            raise TripError("Ungültiges Ziel.") from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise TripError("Ungültige Koordinate.")
        st = {"lat": lat, "lon": lon, "name": str(s.get("name") or f"Punkt {len(out) + 1}")[:120], "stop": bool(s.get("stop"))}
        if s.get("mode") in MODES:   # Einstellung fuer den Abschnitt bis hierher
            st["mode"] = s["mode"]
        stay = s.get("stay")   # Aufenthalt in Minuten
        if isinstance(stay, (int, float)) and not isinstance(stay, bool) and 0 < stay <= 1440 and not st["stop"]:
            st["stay"] = int(round(stay))
        out.append(st)
    return out


def planner_avoid(a):
    import planner
    return planner.avoid_flags(a)


def save(user, data):
    """Anlegen oder (mit vorhandener id) aktualisieren -> gespeicherte Reise."""
    name = str(data.get("name") or "Reise").strip()
    if len(name) > MAX_NAME:
        raise TripError(f"Name zu lang (max. {MAX_NAME} Zeichen).")
    stops = _stops(data.get("stops"))
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    old = get(user, data.get("id")) if data.get("id") else None
    if not old and _count(user) >= MAX_TRIPS:
        raise TripError(f"Schon {MAX_TRIPS} Touren gespeichert – bitte erst alte löschen.")
    trip = {"id": old["id"] if old else secrets.token_hex(6), "name": name,
            "created": old["created"] if old else now, "updated": now,
            "vehicle": _vehicle(data.get("vehicle")), "stops": stops,
            "mode": data.get("mode") if data.get("mode") in MODES else "fast",
            "avoid": planner_avoid(data.get("avoid"))}   # Maut/Faehren meiden – auch fuers Reisepaket
    # „Meine Touren“: kompletter Planer-Stand + Notiz; fehlt er beim Speichern, bleibt der alte
    plan = data["plan"] if "plan" in data else (old or {}).get("plan")
    if plan is not None:
        if not isinstance(plan, dict) or len(json.dumps(plan, ensure_ascii=False)) > MAX_PLAN:
            raise TripError("Tour zu groß zum Speichern.")
        trip["plan"] = plan
    # Fahrpausen der Reise – fuers Navi; fehlt das Feld, bleibt der alte Wert
    if "pause" in data:
        trip["pause"] = data["pause"] if data["pause"] in PAUSES else "auto"
    elif (old or {}).get("pause"):
        trip["pause"] = old["pause"]
    note = data["note"] if "note" in data else (old or {}).get("note")
    if note:
        trip["note"] = str(note)[:MAX_NOTE]
    if old:
        _keep_version(user, old)
    os.makedirs(_dir(user), exist_ok=True)
    path = _path(user, trip["id"])
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(trip, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)
    return trip


def _vdir(user, trip_id):
    return os.path.join(_dir(user), trip_id + ".versions")


def _keep_version(user, old):
    """Vorigen Stand aufheben (die letzten KEEP_VERSIONS je Tour)."""
    d = _vdir(user, old["id"])
    os.makedirs(d, exist_ok=True)
    stamp = re.sub(r"[^0-9T]", "", old.get("updated", "")) or str(int(time.time()))
    with open(os.path.join(d, stamp + ".json"), "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False)
    for name in sorted(os.listdir(d))[:-KEEP_VERSIONS]:
        os.remove(os.path.join(d, name))


def versions(user, trip_id):
    """Fruehere Staende einer Tour, neueste zuerst: [{v, updated, name, stops}]."""
    if not _path(user, trip_id):
        return []
    d = _vdir(user, trip_id)
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d), reverse=True):
        with open(os.path.join(d, name), encoding="utf-8") as f:
            t = json.load(f)
        out.append({"v": name[:-5], "updated": t.get("updated"), "name": t.get("name"), "stops": len(t.get("stops", []))})
    return out


def restore(user, trip_id, v):
    """Frueheren Stand zurueckholen – der aktuelle wird dabei selbst zur Version."""
    if not re.match(r"^[0-9T]{6,20}$", v or "") or not get(user, trip_id):
        return None
    path = os.path.join(_vdir(user, trip_id), v + ".json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        t = json.load(f)
    return save(user, {**t, "id": trip_id})


def copy(user, trip_id):
    t = get(user, trip_id)
    if not t:
        return None
    return save(user, {**{k: v for k, v in t.items() if k != "id"}, "name": f"Kopie von {t['name']}"[:MAX_NAME]})


def get(user, trip_id):
    path = _path(user, trip_id)
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _count(user):
    d = _dir(user)
    if not os.path.isdir(d):
        return 0
    return sum(1 for f in os.listdir(d) if f.endswith(".json") and re.match(ID_RE, f[:-5]))


def used_bytes(user):
    """Belegter Platz (Touren, Versionen, Analysen) eines Logins."""
    total = 0
    for root, _dirs, files in os.walk(_dir(user)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def list_(user):
    d = _dir(user)
    if not os.path.isdir(d):
        return []
    out = [get(user, f[:-5]) for f in os.listdir(d) if f.endswith(".json")]
    return sorted((t for t in out if t), key=lambda t: t["updated"], reverse=True)


def _apath(user, trip_id):
    """Gespeicherte Analyse einer Tour (0.28.1): <id>.analysis.gz – endet nicht auf .json, list_ sieht sie nicht."""
    p = _path(user, trip_id)
    return p[:-5] + ".analysis.gz" if p else None


def save_analysis(user, trip_id, gpx_hash, result):
    """Letzte Analyse zur Tour ablegen (ersetzt die vorige). gpx_hash sagt, zu welcher geplanten Route sie gehoert."""
    if not get(user, trip_id):
        return None
    if not isinstance(gpx_hash, str) or not re.match(r"^[0-9a-f]{16,64}$", gpx_hash) or not isinstance(result, dict):
        raise TripError("Ungültige Analyse.")
    raw = json.dumps({"gpx_hash": gpx_hash, "saved": time.strftime("%Y-%m-%dT%H:%M:%S"), "result": result},
                     ensure_ascii=False).encode()
    if len(raw) > MAX_ANALYSIS:
        raise TripError("Analyse zu groß zum Speichern.")
    path = _apath(user, trip_id)
    old_size = os.path.getsize(path) if os.path.exists(path) else 0
    if used_bytes(user) - old_size + len(raw) // 4 > MAX_USER_BYTES:   # gzip-Groesse grob geschaetzt, vor dem Schreiben
        raise TripError("Dein Speicherplatz für Touren ist voll – bitte alte Touren oder Analysen löschen.")
    with gzip.open(path + ".tmp", "wb", compresslevel=6) as f:
        f.write(raw)
    os.replace(path + ".tmp", path)
    return analysis_info(user, trip_id)


def load_analysis(user, trip_id):
    path = _apath(user, trip_id)
    if not path or not os.path.isfile(path):
        return None
    with gzip.open(path, "rb") as f:
        return json.loads(f.read())


def analysis_info(user, trip_id):
    """Nur ob/wann es eine gibt (fuer die Tourenliste), ohne das grosse JSON zu lesen."""
    path = _apath(user, trip_id)
    if not path or not os.path.isfile(path):
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(path)))


def delete(user, trip_id):
    path = _path(user, trip_id)
    if not path or not os.path.isfile(path):
        return False
    os.remove(path)
    ap = _apath(user, trip_id)
    if ap and os.path.isfile(ap):
        os.remove(ap)
    d = _vdir(user, trip_id)
    if os.path.isdir(d):
        for name in os.listdir(d):
            os.remove(os.path.join(d, name))
        os.rmdir(d)
    return True
