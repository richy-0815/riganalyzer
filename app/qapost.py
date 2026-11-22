"""QA-Postfach (seit 0.40.1, Ticket #10): Mails, die die App in QA verschicken wuerde, im Browser ansehen.

QA laeuft mit MAIL_OFF=1 – mailer.send verwirft jede Mail. Nur mit STAGE=qa UND MAIL_OFF=1 legt es sie hier ab
(DATA_DIR/qa-postfach.json, die letzten MAX, aelteste fliegen raus), damit Links mit Token (Mail bestaetigen,
Passwort neu) in QA testbar sind. In Prod (STAGE != qa) wird nie etwas abgelegt; die Seite /qa/postfach (main.py)
antwortet dort 404. Ansehen nur fuer Admins mit bestandener 2FA. Der QA-Seed leert das Postfach bei jedem Deploy.
"""

import json
import os
import re
import secrets
import tempfile
import threading
import time
from html import escape

MAX = 50
_lock = threading.Lock()


def active():
    return os.environ.get("STAGE") == "qa" and os.environ.get("MAIL_OFF") == "1"


def _path():
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "qa-postfach.json")


def items():
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f).get("mails", [])
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _write(mails):
    p = _path()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p) or ".", suffix=".qapost")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"mails": mails}, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def store(to, subject, body, app=None):
    """Mail ablegen (nur QA); True, wenn abgelegt. Fehler beim Schreiben halten nichts auf."""
    if not active():
        return False
    m = {"id": secrets.token_hex(6), "t": time.time(), "to": str(to), "subject": str(subject),
         "body": str(body), "app": app or ""}
    try:
        with _lock:
            _write((items() + [m])[-MAX:])
    except OSError:
        return False
    return True


def clear():
    """Postfach leeren (QA-Seed); True, wenn vorher etwas drin war."""
    with _lock:
        had = bool(items())
        try:
            os.remove(_path())
        except FileNotFoundError:
            pass
    return had


_URL = re.compile(r"https?://[^\s<>\"']+")


def _linkify(text):
    """Text sicher als HTML, Links darin klickbar (nur http/https)."""
    out, pos = [], 0
    for m in _URL.finditer(text):
        out.append(escape(text[pos:m.start()]))
        u = escape(m.group(0))
        out.append(f'<a href="{u}" rel="noopener">{u}</a>')
        pos = m.end()
    out.append(escape(text[pos:]))
    return "".join(out)


def page_body(mails):
    """HTML-Inhalt der Seite: neueste zuerst, jede Mail aufklappbar."""
    if not mails:
        return '<p class="hint">Noch keine Mails – das Postfach wird bei jedem QA-Deploy geleert.</p>'
    rows = []
    for m in reversed(mails):
        when = time.strftime("%d.%m. %H:%M:%S", time.localtime(float(m.get("t", 0))))
        app = f' · {escape(m["app"])}' if m.get("app") else ""
        rows.append(f'<details class="qa-mail" data-id="{escape(str(m.get("id", "")))}"><summary><b>{escape(m.get("subject", ""))}</b>'
                    f' <span class="hint">an {escape(m.get("to", ""))} · {when}{app}</span></summary>'
                    f'<pre style="white-space:pre-wrap;font:inherit;margin:8px 0">{_linkify(m.get("body", ""))}</pre></details>')
    return (f'<p class="hint">{len(mails)} Mail(s), neueste oben – nur QA, nichts davon wurde verschickt.</p>'
            + "".join(rows))
