"""Rueckmeldungen aus der App (seit v0.35.0): nur angemeldet, je App getrennt, Bremse je Login und Tag.

Liegt in DATA_DIR/feedback.json. Jede neue Rueckmeldung geht als Mail an mail2telegram auf dem Server (Alarm-Kanal) –
im Hintergrund, ein Fehler beim Versand stoert das Speichern nie."""

import datetime as dt
import json
import logging
import os
import tempfile
import threading
import uuid

import mailer

log = logging.getLogger("feedback")

TO = os.environ.get("FEEDBACK_NOTIFY_TO", "telegram@app.internal")
KINDS = {"fehler": "Fehler", "idee": "Idee", "lob": "Lob", "sonstiges": "Sonstiges"}
APP = {"rig": "RigAnalyzer", "ride": "RideAnalyzer"}
MIN_TEXT, MAX_TEXT, MAX_PAGE = 3, 4000, 200
PER_DAY = 10
_lock = threading.Lock()


class FeedbackError(ValueError):
    """Eingabe ungueltig (422)."""


class TooMany(Exception):
    """Tagesgrenze erreicht (429)."""


def _path():
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "feedback.json")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []


def _save(items):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _async(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True, name="feedback").start()


def _notify(entry, mail):
    subj = f"💬 Rückmeldung von {entry['user']} ({APP.get(entry['app'], entry['app'])}) – {KINDS[entry['kind']]}"
    body = entry["text"] + "\n\n" + " · ".join(x for x in (
        f"Seite {entry['page']}" if entry.get("page") else "", f"Antwort an {mail}" if mail else "ohne Mailadresse") if x)
    try:
        mailer.send(TO, subj, body)
    except Exception as err:   # noqa: BLE001 – Versand ist Zusatz
        log.warning("Rueckmeldung nicht gemeldet: %s", err)


def add(user, app, kind, text, page="", mail=None, now=None):
    """Rueckmeldung pruefen, speichern, melden -> Eintrag. FeedbackError bei ungueltiger Eingabe, TooMany ab der 11. am Tag."""
    now = now or dt.datetime.now()
    text = (text or "").strip() if isinstance(text, str) else ""
    if kind not in KINDS:
        raise FeedbackError("Bitte eine Art wählen: Fehler, Idee, Lob oder Sonstiges.")
    if not MIN_TEXT <= len(text) <= MAX_TEXT:
        raise FeedbackError(f"Bitte {MIN_TEXT} bis {MAX_TEXT} Zeichen schreiben.")
    # einzeilig, ohne Steuerzeichen (landet im Ticket-Kopf)
    page = " ".join("".join(c if c.isprintable() else " " for c in page).split())[:MAX_PAGE] if isinstance(page, str) else ""
    entry = {"id": uuid.uuid4().hex[:12], "t": now.isoformat(timespec="seconds"), "user": user, "app": app,
             "kind": kind, "text": text, "page": page, "done": False}
    with _lock:
        items = _load()
        today = now.date().isoformat()
        if sum(1 for x in items if x["user"] == user and x["t"].startswith(today)) >= PER_DAY:
            raise TooMany()
        items.append(entry)
        _save(items)
    _async(_notify, entry, mail)
    return entry


def items(app):
    """Alle Rueckmeldungen einer App, neueste zuerst."""
    with _lock:
        return sorted((x for x in _load() if x["app"] == app), key=lambda x: x["t"], reverse=True)


def set_done(app, fid, done):
    """Haken setzen/entfernen -> True, wenn es die Rueckmeldung in dieser App gibt."""
    with _lock:
        lst = _load()
        hit = next((x for x in lst if x["id"] == fid and x["app"] == app), None)
        if hit is None:
            return False
        hit["done"] = bool(done)
        _save(lst)
        return True


def set_ticket(app, fid, on):
    """Als Ticket vormerken (ticket="vorgemerkt") bzw. Vormerkung entfernen -> True, wenn es die Rueckmeldung gibt.
    Das Issue legt der Rueckmeldungs-Abgleich auf dem Mac an (seit v0.39.0)."""
    with _lock:
        lst = _load()
        hit = next((x for x in lst if x["id"] == fid and x["app"] == app), None)
        if hit is None:
            return False
        if on:
            hit["ticket"] = "vorgemerkt"
        else:
            hit.pop("ticket", None)
        _save(lst)
        return True


class NotDone(Exception):
    """Rueckmeldung ist noch nicht abgehakt – erst erledigt, dann loeschen (409, seit v0.43.0)."""


def delete(app, fid):
    """Eine abgehakte Rueckmeldung dieser App loeschen -> geloeschter Eintrag, None wenn es sie nicht gibt.
    NotDone, wenn sie noch offen ist. Ein angelegtes Ticket bleibt bestehen (Kosten haengen am Ticket)."""
    with _lock:
        lst = _load()
        hit = next((x for x in lst if x["id"] == fid and x["app"] == app), None)
        if hit is None:
            return None
        if not hit.get("done"):
            raise NotDone()
        _save([x for x in lst if x is not hit])
        return hit


def delete_done(app):
    """Alle abgehakten Rueckmeldungen dieser App loeschen -> Anzahl. Offene und die der anderen App bleiben."""
    with _lock:
        lst = _load()
        keep = [x for x in lst if x["app"] != app or not x.get("done")]
        n = len(lst) - len(keep)
        if n:
            _save(keep)
        return n
