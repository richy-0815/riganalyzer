"""Mailadresse im Konto aendern (seit 0.37.6): Bestaetigungslink an die neue Adresse, Hinweis an die alte.

Muster wie reset.py: gespeichert wird nur der SHA-256 des Tokens, dazu der Passwort-Hash beim Ausstellen – wechselt
das Passwort vorher, ist der Link ungueltig. Je Login hoechstens ein offener Wechsel; ein neuer ersetzt den alten.
Das Passwort prueft main.py (Sperre gegen Durchprobieren wie beim Login), nicht dieses Modul."""

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
_lock = threading.Lock()


class Invalid(Exception):
    pass


class Throttled(Exception):
    pass


class SendFailed(Exception):
    pass


def _file():
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "mailchange.json")


def _load():
    try:
        with open(_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"pending": {}, "sent": {}}


def _save(d):
    tmp = _file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.chmod(tmp, 0o600)   # enthaelt Passwort-Hashes (wie users.json)
    os.replace(tmp, _file())


def _hash(tok):
    return hashlib.sha256((tok or "").encode()).hexdigest()


def mask(mail):
    local, _, domain = (mail or "").partition("@")
    return f"{local[:1]}…@{domain}"


def _app_name(app):
    return mailer.APP_NAME.get(app, "RideAnalyzer/RigAnalyzer")


def _notice(to, name, what, app):
    """Hinweis an die bisherige Adresse – im Hintergrund, ein Fehler dabei haelt nichts auf."""
    body = (f"Hallo {name},\n\nfür deinen Login {what}\n\n"
            "Warst du das nicht, ändere bitte dein Passwort und melde dich bei Richy.\n")
    threading.Thread(target=mailer.send, args=(to, f"Deine Mailadresse beim {_app_name(app)} soll geändert werden",
                                               body, app), daemon=True).start()


def request(name, new_mail, base_url, app=None):
    new_mail = admin._mail(new_mail)
    if not new_mail:
        raise admin.AdminError("Mail: bitte eine Adresse eintragen.")
    u = auth.users().get(name) or {}
    old = u.get("mail") or ""
    if new_mail.lower() == old.lower():
        return {"status": "unchanged"}
    now = time.time()
    tok = secrets.token_urlsafe(32)
    with _lock:
        d = _load()
        sent = [t for t in d["sent"].get(name, []) if now - t < 3600]
        if len(sent) >= MAX_PER_HOUR:
            raise Throttled()
        d["pending"][name] = {"hash": _hash(tok), "mail": new_mail, "exp": now + TTL, "pw": u.get("hash")}
        d["sent"][name] = sent + [now]
        _save(d)
    ok = mailer.send(new_mail, f"Neue Mailadresse für den {_app_name(app)}",
                     f"Hallo {name},\n\ndiese Adresse soll künftig zu deinem Login gehören. Bitte bestätige das über "
                     f"diesen Link (1 Stunde gültig, nur einmal):\n\n{base_url}/mail-bestaetigen?t={tok}\n\n"
                     "Warst du das nicht, ignorier die Mail einfach – es ändert sich nichts.\n", app)
    if not ok and os.environ.get("MAIL_OFF") != "1":   # QA verschickt nie Mails – dort gilt der Link als verschickt
        drop(name)
        raise SendFailed()
    if old:
        _notice(old, name, f"soll die Mailadresse auf {mask(new_mail)} geändert werden. Sie gilt erst, wenn der Link "
                           "an die neue Adresse bestätigt ist.", app)
    return {"status": "sent", "pending": new_mail, "expires": now + TTL}


def remove(name, app=None):
    """Mailadresse sofort entfernen (nichts zu bestaetigen) und die bisherige Adresse informieren."""
    old = (auth.users().get(name) or {}).get("mail") or ""
    admin.update(name, mail="")
    drop(name)
    if old:
        _notice(old, name, "wurde die Mailadresse entfernt. „Passwort vergessen“ geht damit nicht mehr per Mail.", app)


def confirm(token):
    """Uebernimmt die neue Adresse und verbraucht den Link -> (name, mail); wirft Invalid."""
    key = _hash(token)
    # Ganz unter dem Schreib-Lock (Reihenfolge immer WRITE_LOCK -> _lock): setzt ein Admin die Mail
    # gleichzeitig, ist der offene Wechsel danach verworfen und dieser Link ungueltig – der Admin gewinnt.
    with auth.WRITE_LOCK:
        with _lock:
            d = _load()
            name = next((n for n, p in d["pending"].items() if p["hash"] == key), None)
            p = d["pending"].pop(name, None) if name else None
            _save(d)
        if not p or p["exp"] < time.time():
            raise Invalid()
        u = auth.read_disk().get(name)
        if not u or u.get("locked") or u["hash"] != p["pw"]:
            raise Invalid()
        admin.update(name, mail=p["mail"])
    return name, p["mail"]


def pending(name):
    p = _load()["pending"].get(name)
    if not p or p["exp"] < time.time():
        return None
    return {"mail": p["mail"], "expires": p["exp"]}


def cancel(name):
    with _lock:
        d = _load()
        had = d["pending"].pop(name, None) is not None
        if had:
            _save(d)
    return had


def drop(name):
    cancel(name)


def forget(pred):
    """Offene Wechsel und Mengenbremse aller Logins mit pred(name) loeschen (QA-Seed) -> True, wenn geaendert."""
    with _lock:
        d = _load()
        new = {k: {n: v for n, v in d.get(k, {}).items() if not pred(n)} for k in ("pending", "sent")}
        if new == {k: d.get(k, {}) for k in ("pending", "sent")}:
            return False
        _save(new)
    return True
