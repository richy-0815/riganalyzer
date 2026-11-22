"""Selbst registrieren (seit v0.20.0): /registrieren legt einen GESPERRTEN Login an ("pending": true),
die Oberadmins bekommen eine Mail und schalten ihn im Admin-Bereich frei („✅ Freischalten“).
Der Login bekommt nur die App der Seite, auf der er sich registriert. Bremsen: Honeypot-Feld, hoechstens
IP_MAX Registrierungen je IP und Stunde, hoechstens MAX_PENDING offene Antraege insgesamt."""

import re
import threading
import time

import admin
import auth

IP_MAX, IP_WINDOW = 3, 3600
ATTEMPT_MAX = 10   # Versuche je IP und Stunde insgesamt (auch abgelehnte) – sonst Login-Namen raten ohne Bremse
MAX_PENDING = 20
NOTE_MAX = 300
_lock = threading.Lock()
_ip_hits = {}
_ip_tries = {}


class RegisterError(ValueError):
    pass


class MailTaken(RegisterError):
    """Mailadresse gehoert schon einem Login (0.46.1): das Formular sagt dasselbe wie bei Erfolg, die Adresse bekommt
    einen Hinweis per Mail – sonst liesse sich ohne Anmeldung pruefen, wer hier Kunde ist."""
    def __init__(self, owner):
        super().__init__("Mailadresse schon vergeben")
        self.owner = owner


def ip_throttled(ip):
    """Zaehlt nur erfolgreiche Antraege (note_ip), Tippfehler im Formular kosten nichts."""
    now = time.time()
    with _lock:
        hits = [t for t in _ip_hits.get(ip, []) if now - t < IP_WINDOW]
        _ip_hits[ip] = hits
        return len(hits) >= IP_MAX


def attempt_throttled(ip):
    """Jeder Versuch zaehlt (pruefen und zaehlen in einem Schritt)."""
    now = time.time()
    with _lock:
        tries = [t for t in _ip_tries.get(ip, []) if now - t < IP_WINDOW]
        if len(tries) >= ATTEMPT_MAX:
            _ip_tries[ip] = tries
            return True
        tries.append(now)
        _ip_tries[ip] = tries
        return False


def note_ip(ip):
    with _lock:
        _ip_hits.setdefault(ip, []).append(time.time())


def pending_count():
    return sum(1 for u in auth.users().values() if u.get("pending"))


def _taken(data, name, mail):
    if name in data:
        raise RegisterError("Den Benutzernamen gibt es schon – bitte einen anderen wählen.")
    owner = next((n for n, u in data.items() if (u.get("mail") or "").lower() == mail.lower()), None)
    if owner:
        raise MailTaken(owner)


def create(name, mail, pw, pw2, note, app):
    """Legt den gesperrten Login an -> Name; wirft RegisterError mit Text fuer das Formular."""
    name = (name or "").strip().lower()
    mail = (mail or "").strip()
    note = re.sub(r"\s+", " ", (note or "")).strip()[:NOTE_MAX]
    if not re.match(admin.NAME_RE, name):
        raise RegisterError("Benutzername: 2–32 Kleinbuchstaben oder Ziffern, ohne Leer- und Sonderzeichen.")
    if not mail or not re.match(admin.MAIL_RE, mail):
        raise RegisterError("Bitte eine gültige Mailadresse angeben – darüber kommt die Freischaltung.")
    if admin.pw_problem(pw):
        raise RegisterError(admin.pw_problem(pw))
    if pw != pw2:
        raise RegisterError("Die beiden Passwörter sind verschieden.")
    if app not in admin.APPS:
        raise RegisterError("Unbekannte App.")
    _taken(auth.read_disk(), name, mail)   # erst pruefen, dann bcrypt (sonst kostet jeder Versuch einen Hash)
    h = auth.hash_pw(pw)
    with auth.WRITE_LOCK:
        data = auth.read_disk()
        _taken(data, name, mail)
        if sum(1 for u in data.values() if u.get("pending")) >= MAX_PENDING:
            raise RegisterError("Gerade liegen zu viele offene Anfragen vor – bitte später nochmal versuchen.")
        data[name] = {"hash": h, "apps": [app], "mail": mail, "locked": True, "pending": True,
                      "registered": time.strftime("%Y-%m-%dT%H:%M:%S"), "pw_changed": time.strftime("%Y-%m-%dT%H:%M:%S"),
                      **({"note": note} if note else {})}
        auth.save_users(data)
    return name
