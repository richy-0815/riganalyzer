"""Ereignis-Tagebuch (seit v0.16.0): wichtige Vorgaenge dauerhaft je Tag in DATA_DIR/events/JJJJ-MM-TT.jsonl,
90 Tage. Grundlage fuer den Tagesbericht – der Zugriffslog des Containers ist nach jedem Deploy leer.
Nie Passwoerter, Tokens oder Positionen."""

import datetime as dt
import json
import logging
import os
import threading

DIR = os.path.join(os.environ.get("DATA_DIR", "/data"), "events")
KEEP_DAYS = 90
SECRET = {"password", "pw", "hash", "token", "secret", "lat", "lon", "position"}
_lock = threading.Lock()


def log(kind, user=None, app=None, now=None, **data):
    now = now or dt.datetime.now()
    e = {"t": round(now.timestamp(), 1), "kind": kind, "user": user, "app": app,
         **{k: v for k, v in data.items() if k not in SECRET}}
    try:
        with _lock:
            os.makedirs(DIR, exist_ok=True)
            with open(os.path.join(DIR, f"{now.date().isoformat()}.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    except OSError as err:   # Tagebuch ist Zusatz – nie die eigentliche Aktion stoeren
        logging.getLogger("events").debug("Ereignis nicht geschrieben: %s", err)


def read_day(day):
    try:
        with open(os.path.join(DIR, f"{day.isoformat()}.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def prune(today=None):
    cut = (today or dt.date.today()) - dt.timedelta(days=KEEP_DAYS)
    try:
        names = os.listdir(DIR)
    except FileNotFoundError:
        return
    for n in names:
        try:
            if dt.date.fromisoformat(n[:10]) < cut:
                os.remove(os.path.join(DIR, n))
        except ValueError:
            continue


class ErrorHandler(logging.Handler):
    """Warnungen/Fehler der App ins Tagebuch (ohne Zugriffslog und HTTP-Bibliotheken)."""
    SKIP = ("zugriff", "httpx", "httpcore", "uvicorn.access", "events")

    def emit(self, record):
        if record.name.startswith(self.SKIP):
            return
        try:
            log("fehler", logger=record.name, level=record.levelname, msg=record.getMessage()[:300])
        except Exception:   # noqa: BLE001 – Logging darf nie selbst scheitern
            pass
