"""Benutzerverwaltung fuer Admins (seit v0.9.2): Logins, Passwoerter, Mail, Apps, Sperre.
Der letzte Oberadmin kann weder sein Admin-Recht verlieren noch gesperrt oder geloescht werden.
Gruppen-Admins (seit v0.14, scope={"apps", "groups"}) sehen und aendern nur Logins ohne Admin-Recht aus ihren Gruppen,
deren Apps in ihren Admin-Apps liegen. Gruppen (groups.json neben users.json) verwaltet nur der Oberadmin."""

import json
import os
import re
import secrets
import string
import tempfile
import time

import auth

_lock = auth.WRITE_LOCK   # gemeinsame Sperre mit Login-Zeitstempel und Passwort-Link
NAME_RE = r"^[a-z0-9]{2,32}$"
# ohne , ; < > " ( ) [ ] \\ – sonst wird „x,ich@evil.example“ zu zwei Empfaengern
MAIL_RE = r"^[^@\s,;<>\"()\[\]\\]+@[^@\s,;<>\"()\[\]\\]+\.[^@\s,;<>\"()\[\]\\]+$"
APPS = ("ride", "rig")
MIN_PW, GEN_PW = 10, 14


def pw_problem(pw):
    """Fehlertext fuer ein neues Passwort oder None (Ticket #16: auch die bcrypt-Obergrenze von 72 Byte)."""
    if len(pw or "") < MIN_PW:
        return f"Passwort: mindestens {MIN_PW} Zeichen."
    if len((pw or "").encode()) > auth.MAX_PW_BYTES:
        return f"Passwort: höchstens {auth.MAX_PW_BYTES} Byte (ca. 70 Zeichen, Umlaute zählen doppelt)."
    return None


class AdminError(ValueError):
    status = 422


class LastAdmin(AdminError):
    status = 409

    def __init__(self):
        super().__init__("Das ist der letzte Admin – erst einem anderen Login Admin-Rechte geben.")


class Forbidden(AdminError):
    status = 403


class Unknown(AdminError):
    status = 404

    def __init__(self):
        super().__init__("Login unbekannt.")


def gen_password():
    a = string.ascii_letters + string.digits
    return "".join(secrets.choice(a) for _ in range(GEN_PW))


# ---------------------------------------------------------------- Gruppen
def _groups_file():
    return os.path.join(os.path.dirname(auth.USERS_FILE) or ".", "groups.json")


def read_groups():
    try:
        with open(_groups_file(), encoding="utf-8") as f:
            return json.load(f).get("groups", {})
    except FileNotFoundError:
        return {}


def _save_groups(groups):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_groups_file()), suffix=".groups")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"groups": groups}, f, indent=1, ensure_ascii=False, sort_keys=True)
    os.chmod(tmp, 0o644)
    os.replace(tmp, _groups_file())


def _group_name(v, groups, skip=None):
    v = (v or "").strip()
    if not 1 <= len(v) <= 40:
        raise AdminError("Gruppenname: 1–40 Zeichen.")
    if any(g["name"].lower() == v.lower() for gid, g in groups.items() if gid != skip):
        raise AdminError("Die Gruppe gibt es schon.")
    return v


def list_groups(scope="all"):
    users = auth.users()
    mine = None if scope == "all" else set(scope["groups"])
    return [{"id": gid, "name": g["name"], "members": sum(1 for u in users.values() if u.get("group") == gid)}
            for gid, g in sorted(read_groups().items(), key=lambda x: x[1]["name"].lower()) if mine is None or gid in mine]


def create_group(name):
    with _lock:
        groups = read_groups()
        gid = "g" + secrets.token_hex(4)
        groups[gid] = {"name": _group_name(name, groups)}
        _save_groups(groups)
    return {"id": gid, "name": groups[gid]["name"], "members": 0}


def rename_group(gid, name):
    with _lock:
        groups = read_groups()
        if gid not in groups:
            raise AdminError("Gruppe unbekannt.")
        groups[gid] = {**groups[gid], "name": _group_name(name, groups, skip=gid)}
        _save_groups(groups)
    return {"id": gid, "name": groups[gid]["name"]}


def delete_group(gid):
    """Mitglieder bleiben als Logins ohne Gruppe, Gruppen-Admins verlieren die Gruppe."""
    with _lock:
        groups = read_groups()
        if gid not in groups:
            raise AdminError("Gruppe unbekannt.")
        data = auth.read_disk()
        for n, u in data.items():
            u = dict(u)
            if u.get("group") == gid:
                u.pop("group")
            sc = auth.admin_scope(u)
            if isinstance(sc, dict) and gid in sc["groups"]:
                u["admin"] = {**sc, "groups": [g for g in sc["groups"] if g != gid]}
            data[n] = u
        auth.save_users(data)
        del groups[gid]
        _save_groups(groups)


# ---------------------------------------------------------------- Letzte Aenderungen
LOG_MAX = 300


def _log_file():
    return os.path.join(os.path.dirname(auth.USERS_FILE) or ".", "admin-log.json")


def read_log():
    try:
        with open(_log_file(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def record(who, target, what):
    """Wer hat wann was an welchem Login/welcher Gruppe geaendert – nie Passwoerter, nur dass."""
    import events
    events.log("admin", who, None, target=target, what=what)
    with _lock:
        log = ([{"t": time.time(), "who": who, "target": target, "what": what}] + read_log())[:LOG_MAX]
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_log_file()), suffix=".log")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _log_file())


def log_for(me, scope, app=None):
    """Oberadmin: alles (zu Logins nur der App dieser Seite). Gruppen-Admin: eigene Aenderungen und Logins, die er sieht."""
    users = auth.users()
    if scope == "all":
        return [e for e in read_log() if e["target"] not in users or visible(users[e["target"]], scope, app)]
    return [e for e in read_log() if e["who"] == me or (e["target"] in users and visible(users[e["target"]], scope, app))]


def rights_text(a):
    if a is True:
        return "Oberadmin"
    if not a:
        return "kein Admin"
    names = {gid: g["name"] for gid, g in read_groups().items()}
    return "Gruppen-Admin " + "+".join({"ride": "Ride", "rig": "Rig"}[x] for x in a["apps"]) + " · " + \
        ", ".join(names.get(g, g) for g in a["groups"])


# ---------------------------------------------------------------- Rechte
def _public(name, u):
    return {"name": name, "mail": u.get("mail", ""), "apps": u.get("apps", []), "admin": _admin_out(u),
            "group": u.get("group"), "locked": bool(u.get("locked")), "last_login": u.get("last_login"),
            "pw_initial": bool(u.get("pw_initial")), "pw_changed": u.get("pw_changed"),
            "pending": bool(u.get("pending")), "registered": u.get("registered"), "note": u.get("note", ""),
            "notify": _notify(name)}   # waehlbare Mails: nur anzeigen, aendern kann nur der Nutzer


def _notify(name):
    import notify   # spaet: notify -> prefs/mailer, hier nur zum Anzeigen
    return notify.settings(name)


def _admin_out(u):
    sc = auth.admin_scope(u)
    return True if sc == "all" else sc or False


def _admin_in(v):
    """True/"all" = Oberadmin, {"apps": [...], "groups": [...]} = Gruppen-Admin, sonst kein Admin."""
    if v in (True, "all"):
        return True
    if v in (False, None, ""):
        return False
    if isinstance(v, dict):
        groups = read_groups()
        if any(g not in groups for g in v.get("groups") or []):
            raise AdminError("Gruppe unbekannt.")
        if not v.get("groups"):
            raise AdminError("Gruppen-Admin braucht mindestens eine Gruppe – sonst sieht er niemanden.")
        return {"apps": _apps(v.get("apps")), "groups": sorted(set(v.get("groups") or []))}
    raise AdminError("Admin-Recht: ja, nein oder Apps + Gruppen.")


def _group_in(v):
    if v in (None, ""):
        return None
    if v not in read_groups():
        raise AdminError("Gruppe unbekannt.")
    return v


def visible(u, scope, app=None):
    """Darf ein Admin mit diesem Umfang den Login sehen/aendern? app (seit 0.17.2): nur Logins dieser App."""
    if app and app not in (u.get("apps") or []):
        return False
    if scope == "all":
        return True
    return (isinstance(scope, dict) and not u.get("admin") and u.get("group") in scope["groups"]
            and set(u.get("apps") or []) <= set(scope["apps"]))


def _target(data, name, scope, app=None):
    if name not in data or not visible(data[name], scope, app):
        raise Unknown()
    return data[name]


def _within(scope, apps=None, group=..., admin=None):
    """Gruppen-Admin: nur seine Apps und Gruppen, keine Admin-Rechte."""
    if scope == "all":
        return
    if admin:
        raise Forbidden("Admin-Rechte vergibt nur der Oberadmin.")
    if apps is not None and not set(apps) <= set(scope["apps"]):
        raise Forbidden("Diese App verwaltest du nicht.")
    if group is not ... and group not in scope["groups"]:
        raise Forbidden("Diese Gruppe verwaltest du nicht.")


def list_users(scope="all", app=None):
    return [_public(n, u) for n, u in sorted(auth.users().items()) if visible(u, scope, app)]


def _mail(v):
    v = (v or "").strip()
    if v and not re.match(MAIL_RE, v):
        raise AdminError("Mailadresse ungültig.")
    return v


def _apps(v):
    apps = [a for a in (v or []) if a in APPS]
    if not apps:
        raise AdminError("Mindestens eine App (ride/rig) wählen.")
    return apps


def _admins(data):
    return [n for n, u in data.items() if auth.admin_scope(u) == "all" and not u.get("locked")]


def create(name, mail="", apps=("ride", "rig"), admin=False, scope="all", group=None, app=None):
    if app:
        apps = [app]
    if scope != "all" and not group and len(scope["groups"]) == 1:
        group = scope["groups"][0]
    _within(scope, apps, group, admin)
    name = (name or "").strip().lower()
    if not re.match(NAME_RE, name):
        raise AdminError("Name: 2–32 Kleinbuchstaben/Ziffern.")
    pw = gen_password()
    with _lock:
        data = auth.read_disk()
        if name in data:
            raise AdminError("Den Login gibt es schon.")
        u = {"hash": auth.hash_pw(pw), "apps": _apps(list(apps)), "mail": _mail(mail), "admin": _admin_in(admin),
             "pw_initial": True}
        if _group_in(group):
            u["group"] = group
        data[name] = u
        auth.save_users(data)
    return pw


def _merge(app, old, sent):
    """Nur die App dieser Seite umschalten, die andere bleibt wie sie ist."""
    return [a for a in APPS if (a != app and a in (old or [])) or (a == app and a in (sent or []))]


def update(name, scope="all", app=None, **f):
    with _lock:
        data = auth.read_disk()
        u = dict(_target(data, name, scope, app))
        if app and "apps" in f:
            f["apps"] = _merge(app, u.get("apps"), f["apps"])
        if app and isinstance(f.get("admin"), dict):
            old = auth.admin_scope(u)
            f["admin"] = {**f["admin"], "apps": _merge(app, old["apps"] if isinstance(old, dict) else [], f["admin"].get("apps"))}
        _within(scope, f.get("apps"), f["group"] if "group" in f else ..., "admin" in f)
        if "mail" in f:
            u["mail"] = _mail(f["mail"])
        if "apps" in f:
            u["apps"] = _apps(f["apps"])
        if "admin" in f:
            u["admin"] = _admin_in(f["admin"])
        if "group" in f:
            g = _group_in(f["group"])
            if g:
                u["group"] = g
            else:
                u.pop("group", None)
        if "locked" in f:
            u["locked"] = bool(f["locked"])
            if not u["locked"] and u.pop("pending", None):
                u.pop("note", None)
        new = {**data, name: u}
        if not _admins(new):
            raise LastAdmin()
        auth.save_users(new)
    return _public(name, u)


def set_password(name, pw=None, scope="all", app=None):
    """Passwort durch einen Admin (oder users.py): gilt als Startpasswort, bis der Nutzer es selbst aendert."""
    pw = pw or gen_password()
    _store_pw(name, pw, own=False, scope=scope, app=app)
    return pw


def set_own_password(name, pw):
    """Der Nutzer setzt sein Passwort selbst (Mein Konto, Passwort-Link). -> True, wenn es das Startpasswort war."""
    return _store_pw(name, pw, own=True)


def _store_pw(name, pw, own, scope="all", app=None):
    if pw_problem(pw):
        raise AdminError(pw_problem(pw))
    with _lock:
        data = auth.read_disk()
        _target(data, name, scope, app)
        u = {k: v for k, v in data[name].items() if k not in ("pw_initial", "pw_changed")}
        u["hash"] = auth.hash_pw(pw)
        if own:
            u["pw_changed"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            u["pw_initial"] = True
        was_initial = bool(data[name].get("pw_initial"))
        data[name] = u
        auth.save_users(data)
    return was_initial


def admin_mails():
    """Mailadressen aller aktiven Admins (fuer Hinweise wie „Startpasswort geaendert“)."""
    return [u["mail"] for u in auth.users().values() if auth.admin_scope(u) == "all" and not u.get("locked") and u.get("mail")]


def delete(name, scope="all", app=None):
    with _lock:
        data = auth.read_disk()
        _target(data, name, scope, app)
        new = {n: u for n, u in data.items() if n != name}
        if not _admins(new):
            raise LastAdmin()
        auth.save_users(new)
