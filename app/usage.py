"""Verbrauchsbuch (seit v0.34.0): je Monat und Login nur ZAEHLER (Downloads, Build-Minuten, Analysen, Planungen,
Routen, Speicher-GB-Tage, aktive Tage) in DATA_DIR/usage/JJJJ-MM.json. Grundlage der Kostenuebersicht.
Nie Namen von Touren, Ziele oder Positionen."""

import datetime as dt
import json
import logging
import os
import tempfile
import threading

COUNTERS = ("dl_bytes", "build_min", "build_bytes", "analyses", "analyse_sec", "plans", "plan_sec", "routes",
            "storage_gbd", "snaps")
_lock = threading.Lock()
_seen = set()
_log = logging.getLogger("usage")


def _dir():
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "usage")


def _path(ym):
    return os.path.join(_dir(), f"{ym}.json")


def _key(user):
    return (user or "").strip().lower()


def _load(ym):
    try:
        with open(_path(ym), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(ym, data):
    os.makedirs(_dir(), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_dir(), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _path(ym))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _user(data, login, app):
    u = data.setdefault("users", {}).setdefault(login, {})
    if app:
        u["app"] = app
    u.setdefault("days", [])
    return u


def _day(u, day):
    if day not in u["days"]:
        u["days"].append(day)
        u["days"].sort()


def _update(ym, fn):
    """Lesen-Aendern-Schreiben unter Lock; Fehler nie nach aussen."""
    try:
        with _lock:
            data = _load(ym)
            fn(data)
            _save(ym, data)
    except Exception as err:   # noqa: BLE001 – Verbrauchsbuch ist Zusatz, nie die eigentliche Aktion stoeren
        _log.debug("Verbrauch nicht geschrieben: %s", err)


def _stamp(data, now):
    data.setdefault("since", now.isoformat(timespec="seconds"))


def add(user, app, now=None, **counts):
    now = now or dt.datetime.now()
    login = _key(user)
    if not login:
        return

    def fn(data):
        _stamp(data, now)
        u = _user(data, login, app)
        for k, v in counts.items():
            if k in COUNTERS:
                u[k] = u.get(k, 0) + v
        _day(u, now.date().isoformat())
    _update(now.strftime("%Y-%m"), fn)


def seen(user, app, now=None):
    now = now or dt.datetime.now()
    login = _key(user)
    day = now.date().isoformat()
    mark = (_dir(), login, day)
    if not login or mark in _seen:
        return
    _seen.add(mark)

    def fn(data):
        _stamp(data, now)
        _day(_user(data, login, app), day)
    _update(now.strftime("%Y-%m"), fn)


def month(ym):
    data = _load(ym)
    return {"since": data.get("since"), "users": data.get("users", {})}


def months():
    try:
        names = os.listdir(_dir())
    except OSError:
        return []
    out = [n[:-5] for n in names if n.endswith(".json") and len(n) == 12]
    return sorted(out, reverse=True)


def backfill(ym, evs):
    def fn(data):
        if "backfilled" in data:
            return
        try:
            since = dt.datetime.fromisoformat(data["since"]).timestamp() if data.get("since") else None
        except (ValueError, TypeError):
            since = None
        for e in evs:
            if since is not None and (e.get("t") or 0) >= since:
                continue   # ab "since" zaehlt das Live-Buch bereits (sonst Doppelzaehlung nach Neustart)
            login = _key(e.get("user"))
            kind = e.get("kind")
            if not login:
                continue
            u = _user(data, login, e.get("app"))
            if kind == "analyse":
                u["analyses"] = u.get("analyses", 0) + 1
                u["analyse_sec"] = u.get("analyse_sec", 0) + (e.get("sec") or 0)
            elif kind == "planung":
                u["plans"] = u.get("plans", 0) + 1
            elif kind == "navi":
                u["routes"] = u.get("routes", 0) + 1
            elif kind == "login" and e.get("ok") is True:
                pass   # nur aktiver Tag
            else:
                continue
            if e.get("t"):
                _day(u, dt.datetime.fromtimestamp(e["t"]).date().isoformat())
        data["backfilled"] = True
    _update(ym, fn)


def _du(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for n in files:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def storage_snapshot(now=None):
    now = now or dt.datetime.now()
    day = now.date().isoformat()
    ym = now.strftime("%Y-%m")
    base = os.environ.get("DATA_DIR", "/data")
    if _load(ym).get("snap") == day:   # billig vorab, ohne Lock
        return
    try:
        sizes = {}   # Verzeichnisbaeume ausserhalb des Locks durchlaufen (koennen viele GB sein)
        for sub in ("packages", "trips"):
            try:
                logins = os.listdir(os.path.join(base, sub))
            except OSError:
                continue
            for name in logins:
                sizes[_key(name)] = sizes.get(_key(name), 0) + _du(os.path.join(base, sub, name))
    except Exception as err:   # noqa: BLE001
        _log.debug("Speicher-Snapshot fehlgeschlagen: %s", err)
        return

    def fn(data):
        if data.get("snap") == day:   # zweiter Lauf am selben Tag: unter Lock erneut pruefen
            return
        _stamp(data, now)
        for login, size in sizes.items():
            if login and size:
                u = _user(data, login, None)
                u["storage_gbd"] = u.get("storage_gbd", 0) + size / 1e9
                u["snaps"] = u.get("snaps", 0) + 1   # Messtage, fuer den Ø belegten Speicher
        data["snap"] = day
    _update(ym, fn)
