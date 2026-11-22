"""„Passwort vergessen“ (seit v0.9.2): Link per Mail, 1 h gueltig, einmalig.
Gespeichert wird nur der SHA-256 des Tokens, dazu der Passwort-Hash beim Ausstellen – wechselt das Passwort
vorher (Admin, Konto), ist der Link ungueltig. Antwort nach aussen immer gleich (keine Auskunft ueber Logins)."""

import hashlib
import json
import os
import secrets
import threading
import time

import admin
import auth
import mailer

TTL = 3600
MAX_PER_HOUR = 3
IP_MAX, IP_WINDOW = 10, 15 * 60
_lock = threading.Lock()
_ip_hits = {}


class Invalid(Exception):
    pass


def _file():
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "reset.json")


def _load():
    try:
        with open(_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"tokens": {}, "sent": {}}


def _save(d):
    tmp = _file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.chmod(tmp, 0o600)   # enthaelt Passwort-Hashes (wie users.json, mailchange.json)
    os.replace(tmp, _file())


def ip_throttled(ip):
    now = time.time()
    with _lock:
        hits = [t for t in _ip_hits.get(ip, []) if now - t < IP_WINDOW]
        _ip_hits[ip] = hits + [now]
        if len(_ip_hits) > 2000:
            for k in [k for k, v in _ip_hits.items() if not v or now - v[-1] >= IP_WINDOW]:
                del _ip_hits[k]
        return len(hits) >= IP_MAX


def _find(who):
    who = (who or "").strip().lower()
    for name, u in auth.users().items():
        if who and (name == who or (u.get("mail") or "").lower() == who):
            return name, u
    return None, None


def request(who, base_url, app=None):
    """Schickt bei Treffer mit Mail einen Link; sonst passiert sichtbar dasselbe (nichts)."""
    name, u = _find(who)
    if not name or not u.get("mail") or u.get("locked"):
        return
    now = time.time()
    with _lock:
        d = _load()
        d["tokens"] = {k: v for k, v in d["tokens"].items() if v["exp"] > now}
        sent = [t for t in d["sent"].get(name, []) if now - t < 3600]
        if len(sent) >= MAX_PER_HOUR:
            return
        tok = secrets.token_urlsafe(32)
        d["tokens"][hashlib.sha256(tok.encode()).hexdigest()] = {"name": name, "exp": now + TTL, "pw": u["hash"]}
        d["sent"][name] = sent + [now]
        _save(d)
    mailer.send(u["mail"], f"Neues Passwort für den {mailer.APP_NAME.get(app, 'RideAnalyzer/RigAnalyzer')}",
                f"Hallo {name},\n\nfür deinen Login wurde ein neues Passwort angefordert. Über diesen Link setzt du es "
                f"(1 Stunde gültig, nur einmal):\n\n{base_url}/passwort-neu?t={tok}\n\n"
                "Warst du das nicht, ignorier die Mail einfach – dein Passwort bleibt, wie es ist.\n", app)


def consume(token, new_pw):
    """Setzt das neue Passwort und verbraucht den Link -> (name, war_startpasswort);
    wirft Invalid oder admin.AdminError (zu kurz)."""
    if admin.pw_problem(new_pw):
        raise admin.AdminError(admin.pw_problem(new_pw))
    key = hashlib.sha256((token or "").encode()).hexdigest()
    with _lock:
        d = _load()
        t = d["tokens"].pop(key, None)
        _save(d)
    if not t or t["exp"] < time.time():
        raise Invalid()
    with auth.WRITE_LOCK:   # Pruefen und Setzen ohne dass ein Admin dazwischen schreibt
        u = auth.read_disk().get(t["name"])
        if not u or u.get("locked") or u["hash"] != t["pw"]:
            raise Invalid()
        was_initial = admin.set_own_password(t["name"], new_pw)
    return t["name"], was_initial
