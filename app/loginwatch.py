"""Anmeldungen in den Telegram-Kanal melden (seit v0.31.1, vorübergehend – LOGIN_NOTIFY=0 schaltet ab).

Browser: jede erfolgreiche Anmeldung (und fehlgeschlagene, damit Durchprobieren auffaellt).
RigNavi/iPad (Basic-Auth bei jeder Anfrage) und Browser mit gespeicherter Anmeldung (Cookie, 30 Tage):
nur das erste Mal je Benutzer + Geraeteart in 6 Stunden.
Versand als Mail an mail2telegram auf dem Server (wie CI-Meldungen und Tagesbericht) – im Hintergrund, nie blockierend.
"""

import logging
import os
import threading
import time

import mailer

log = logging.getLogger("loginwatch")

TO = os.environ.get("LOGIN_NOTIFY_TO", "telegram@app.internal")
APP_QUIET_S = 6 * 3600
_seen = {}
_lock = threading.Lock()
APP = {"rig": "RigAnalyzer", "ride": "RideAnalyzer"}


def enabled():
    return os.environ.get("LOGIN_NOTIFY", "0") == "1"


def _send(subject, body):
    threading.Thread(target=mailer.send, args=(TO, subject, body), daemon=True, name="loginwatch").start()


def login(user, app, ip, ok=True, device="Browser", detail=""):
    """Anmeldung ueber die Login-Seite."""
    if not enabled():
        return
    if ok:   # direkt danach kommt dieselbe Sitzung per Cookie – nicht nochmal als „wieder da“ melden
        with _lock:
            _seen[(user, "Browser")] = time.time()
    when = time.strftime("%d.%m. %H:%M")
    if ok:
        subj = f"🔑 {user} hat sich bei {APP.get(app, app)} angemeldet"
    else:
        subj = f"⚠️ Fehlgeschlagene Anmeldung bei {APP.get(app, app)} ({user or '?'})"
    # keine IP an Telegram (Drittland) – sie steht im Admin-Bereich unter „Jetzt aktiv“
    _send(subj, f"{when} · {device}{(' · ' + detail) if detail else ''}")


def app_seen(user, app, ip, ua):
    """Basic-Auth (RigNavi Android/iPad, Skripte): nur erstes Auftauchen je Benutzer+Geraet in 6 h melden."""
    if not enabled() or not user:
        return
    ua = (ua or "").lower()
    device = ("Browser" if ua == "browser" else "RigNavi Android" if "okhttp" in ua
              else "RigNavi iPad/iPhone" if ("cfnetwork" in ua or "rignavi" in ua) else (ua[:40] or "Programm"))
    key = (user, device)
    now = time.time()
    with _lock:
        if now - _seen.get(key, 0) < APP_QUIET_S:
            return
        _seen[key] = now
    icon, verb = ("👀", "ist wieder da (gespeicherte Anmeldung)") if device == "Browser" else ("📱", f"nutzt {device}")
    _send(f"{icon} {user} {verb} – {APP.get(app, app)}", f"{time.strftime('%d.%m. %H:%M')} · {device}")
