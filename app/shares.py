"""Touren teilen (seit 0.45.0, Ticket #3): nur mit Logins der App, fester Stand, Empfaenger muss annehmen.

Ablage DATA_DIR/shares.json (0600). Eine Einladung haelt den Stand der Tour beim Teilen (Erlaubnisliste
SNAPSHOT_KEYS, nie die Notiz); Annehmen legt daraus mit trips.save eine eigene Tour des Empfaengers an.
Keine Login-Auskunft: auch fuer unbekannte/gesperrte/fremde Logins entsteht beim Absender ein Eintrag
(deliverable=False, ohne Stand), der in keinem Eingang erscheint und bis zum Ablauf „offen“ bleibt."""

import datetime as dt
import json
import os
import re
import secrets
import tempfile
import threading

import auth
import trips

TTL_DAYS = 14
KEEP_DONE_DAYS = 30
PER_DAY = 20
MAX_OPEN = 50
MAX_NAME = 64
SNAPSHOT_KEYS = ("name", "vehicle", "stops", "mode", "avoid", "plan")
ID_RE = re.compile(r"^[0-9a-f]{16}$")
SENT_TEXT = "Wenn es diesen Login gibt, findet er die Tour unter „Für dich geteilt“."
_lock = threading.Lock()


class ShareError(ValueError):
    """Eingabe ungueltig (422)."""


class TooMany(Exception):
    """Bremse erreicht (429)."""


class NotFound(Exception):
    """Gibt es nicht (fuer diesen Login) – 404."""


def _path():
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "shares.json")


def _iso(t):
    return t.isoformat(timespec="seconds")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data.get("shares", []) if isinstance(data, dict) else []
    except FileNotFoundError:
        return []


def _save(items):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"shares": items}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _tidy(items, now):
    """Abgelaufene offene -> „abgelaufen“ (ohne Stand), erledigte nach KEEP_DONE_DAYS weg."""
    cut = _iso(now - dt.timedelta(days=KEEP_DONE_DAYS))
    out = []
    for x in items:
        if x["state"] == "offen" and x["expires"] <= _iso(now):
            x = {**x, "state": "abgelaufen", "done_at": x["expires"]}
            x.pop("snapshot", None)
        if x["state"] != "offen" and x.get("done_at", "") < cut:
            continue
        out.append(x)
    return out


def _norm(name):
    return name.strip().lower() if isinstance(name, str) else ""


def deliverable(name, app="rig"):
    u = auth.users().get(name)
    return bool(u) and not u.get("locked") and app in u.get("apps", [])


PLAN_FLAGS = ("lez", "toll")
PT_STR = re.compile(r"^[a-z_]{1,20}$")


def _num(v, lo, hi):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def _short_list(v, n=50):
    return [x for x in v if isinstance(x, (str, int)) and not isinstance(x, bool) and len(str(x)) <= 80][:n] if isinstance(v, list) else []


def _clean_plan(plan):
    """Planer-Stand fuer jemand anderen (Review 12.11.2026): nur bekannte, einfache Felder – keine Platz-/Ort-Objekte,
    die der Planer per innerHTML zeigt. Fehlende Details holt der Empfaenger beim Neu-Planen selbst."""
    if not isinstance(plan, dict):
        return None
    out = {}
    if isinstance(plan.get("v"), int) and not isinstance(plan.get("v"), bool):
        out["v"] = plan["v"]
    if isinstance(plan.get("mode"), str) and PT_STR.match(plan["mode"]):
        out["mode"] = plan["mode"]
    if isinstance(plan.get("nights"), (str, int)) and not isinstance(plan.get("nights"), bool) and len(str(plan["nights"])) <= 4:
        out["nights"] = plan["nights"]
    for k in ("skip", "avoided"):
        if k in plan:
            out[k] = _short_list(plan[k])
    for k in PLAN_FLAGS + ("ferry",):
        if isinstance(plan.get(k), bool):
            out[k] = plan[k]
    pts = []
    for p in plan.get("pts") if isinstance(plan.get("pts"), list) else []:
        if not isinstance(p, dict) or not _num(p.get("lat"), -90, 90) or not _num(p.get("lon"), -180, 180):
            continue
        q = {"lat": p["lat"], "lon": p["lon"], "name": str(p.get("name") or "")[:120], "stop": p.get("stop") is True}
        if _num(p.get("heading"), -360, 360):
            q["heading"] = p["heading"]
        if isinstance(p.get("mode"), str) and PT_STR.match(p["mode"]):
            q["mode"] = p["mode"]
        if p.get("custom") is True:
            q["custom"] = True
        if _num(p.get("stay"), 1, 1440):   # Aufenthalt in Minuten
            q["stay"] = int(p["stay"])
        pts.append(q)
    out["pts"] = pts[:trips.MAX_POINTS]
    return out


def _snapshot(t):
    snap = {k: t[k] for k in SNAPSHOT_KEYS if k in t and k != "plan"}
    plan = _clean_plan(t.get("plan"))
    if plan is not None:
        snap["plan"] = plan
    return snap


def _stages(stops):
    return sum(1 for s in stops[1:-1] if s.get("stop")) + 1 if stops else 0


def invite(sender, to, trip_id, app="rig", now=None):
    """Einladung anlegen -> Eintrag (zustellbar) oder None. NotFound: Tour gehoert nicht dem Absender."""
    now = now or dt.datetime.now()
    sender, to = _norm(sender), _norm(to)
    if not to or len(to) > MAX_NAME:
        raise ShareError("Bitte einen Login-Namen eingeben.")
    t = trips.get(sender, trip_id)
    if not t:
        raise NotFound()
    snap = _snapshot(t)   # immer bauen – gleiche Arbeit, egal ob zustellbar
    ok = to != sender and deliverable(to, app)
    with _lock:
        items = _tidy(_load(), now)
        today = now.date().isoformat()
        # Bremse zaehlt jedes Schicken, auch Wiederholungen derselben Einladung: je Eintrag
        # sends = {tag: anzahl}; eine Wiederholung erneuert den vorhandenen Eintrag statt einen neuen anzulegen
        sent_today = sum((x.get("sends") or {}).get(today, 0) for x in items if x["from"] == sender)
        same = next((x for x in items if x["from"] == sender and x["to"] == to and x["trip_id"] == trip_id and x["state"] == "offen"), None)
        if sent_today >= PER_DAY:
            raise TooMany(f"Heute schon {PER_DAY} Touren geteilt – bitte morgen wieder.")
        if not same and sum(1 for x in items if x["from"] == sender and x["state"] == "offen") >= MAX_OPEN:
            raise TooMany(f"Schon {MAX_OPEN} offene Einladungen – erst ein paar zurückziehen oder warten.")
        if same:
            e = same
            e.update(name=t["name"], created=_iso(now), expires=_iso(now + dt.timedelta(days=TTL_DAYS)))
        else:
            e = {"id": secrets.token_hex(8), "from": sender, "to": to, "trip_id": trip_id, "name": t["name"],
                 "created": _iso(now), "expires": _iso(now + dt.timedelta(days=TTL_DAYS)), "state": "offen",
                 "deliverable": ok, "app": app}
            items.append(e)
        if ok:
            e["snapshot"] = snap
        e["sends"] = {today: (e.get("sends") or {}).get(today, 0) + 1}   # nur der heutige Tag zaehlt
        _save(items)
    return {**e, "renewed": same is not None} if ok else None


def _view(items, now):
    return _tidy([dict(x) for x in items], now)


def inbox(user, now=None):
    now = now or dt.datetime.now()
    user = _norm(user)
    with _lock:
        items = _view(_load(), now)
    return [{"id": x["id"], "from": x["from"], "name": x["name"], "created": x["created"], "expires": x["expires"],
             "stops": len(x["snapshot"].get("stops", [])), "stages": _stages(x["snapshot"].get("stops", []))}
            for x in items if x["to"] == user and x["state"] == "offen" and x.get("deliverable")]


def outbox(user, now=None):
    now = now or dt.datetime.now()
    user = _norm(user)
    with _lock:
        items = _view(_load(), now)
    return [{"id": x["id"], "to": x["to"], "trip_id": x["trip_id"], "name": x["name"], "created": x["created"],
             "state": x["state"]} for x in items if x["from"] == user]


def _resolve(user, sid, role, now, state):
    """Offene Einladung (role: „to“ oder „from“ == user) auf state setzen -> Eintrag vor der Aenderung."""
    user = _norm(user)
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise NotFound()
    items = _tidy(_load(), now)
    hit = next((x for x in items if x["id"] == sid and x[role] == user and x["state"] == "offen"
                and (role == "from" or x.get("deliverable"))), None)
    if hit is None:
        raise NotFound()
    old = dict(hit)
    hit["state"], hit["done_at"] = state, _iso(now)
    hit.pop("snapshot", None)
    return items, old


def accept(user, sid, now=None):
    """Annehmen -> neue Tour des Empfaengers (fester Stand + shared_from)."""
    now = now or dt.datetime.now()
    with _lock:
        items, old = _resolve(user, sid, "to", now, "angenommen")
        t = trips.save(_norm(user), old["snapshot"])
        t = _mark(_norm(user), t, {"user": old["from"], "at": _iso(now)})
        _save(items)
    return t


def _mark(user, t, info):
    """shared_from in der gespeicherten Tour vermerken (trips.save kennt das Feld nicht)."""
    t = {k: v for k, v in t.items() if k != "shared_from"}
    t["shared_from"] = info
    path = trips._path(user, t["id"])
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)
    return t


def decline(user, sid, now=None):
    now = now or dt.datetime.now()
    with _lock:
        items, _ = _resolve(user, sid, "to", now, "abgelehnt")
        _save(items)


def withdraw(user, sid, now=None):
    now = now or dt.datetime.now()
    with _lock:
        items, _ = _resolve(user, sid, "from", now, "zurückgezogen")
        _save(items)


def forget_user(name):
    """Login geloescht: Einladungen von und an ihn entfernen -> Anzahl."""
    name = _norm(name)
    with _lock:
        items = _load()
        keep = [x for x in items if x["from"] != name and x["to"] != name]
        n = len(items) - len(keep)
        if n:
            _save(keep)
    return n


def of_user(user):
    """Fuer „Meine Daten“: Metadaten, nie der Stand."""
    user = _norm(user)
    with _lock:
        items = _load()
    meta = lambda x: {k: x[k] for k in ("id", "from", "to", "name", "created", "expires", "state") if k in x}  # noqa: E731
    return {"gesendet": [meta(x) for x in items if x["from"] == user],
            "erhalten": [meta(x) for x in items if x["to"] == user and x.get("deliverable")]}
