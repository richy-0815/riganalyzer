"""Anmeldung fuer Ride-/RigAnalyzer (seit v0.8.2 statt Basic-Auth in Traefik).

- Benutzer in data/users.json: {"users": {name: {"hash": bcrypt, "apps": ["ride", "rig"]}}}
  (die alten htpasswd-Hashes $2y$ werden 1:1 uebernommen)
- Browser: signiertes Cookie (HMAC, Schluessel in data/session.key), "angemeldet bleiben" 30 Tage
- RigNavi & Skripte: weiter HTTP-Basic-Auth gegen dieselben Benutzer
- Bremse: hoechstens MAX_FAILS Fehlversuche je IP in FAIL_WINDOW (zusaetzlich fail2ban am Proxy)
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time

import bcrypt

log = logging.getLogger("auth")

DATA = os.environ.get("DATA_DIR", "/data")
USERS_FILE = os.path.join(DATA, "users.json")
KEY_FILE = os.path.join(DATA, "session.key")
COOKIE = "ra_session"
REMEMBER_DAYS = 30
SESSION_HOURS = 12          # ohne "angemeldet bleiben"
MAX_FAILS, FAIL_WINDOW = 10, 15 * 60

_lock = threading.Lock()
_users = {"mtime": None, "data": {}}
_fails = {}                 # ip -> [zeitstempel]
_ok_cache = {}              # (user, sha256(pw)) -> bis wann gueltig (Basic-Auth bei jedem Request)


def _key():
    try:
        with open(KEY_FILE, "rb") as f:
            return f.read()
    except FileNotFoundError:
        k = secrets.token_bytes(32)
        with open(KEY_FILE + ".tmp", "wb") as f:
            f.write(k)
        os.replace(KEY_FILE + ".tmp", KEY_FILE)
        os.chmod(KEY_FILE, 0o600)
        return k


def users():
    """users.json, bei Aenderung automatisch neu geladen (kein Neustart fuer neue Logins)."""
    try:
        m = os.path.getmtime(USERS_FILE)
    except OSError:
        return {}
    with _lock:
        if m != _users["mtime"]:
            with open(USERS_FILE, encoding="utf-8") as f:
                _users["data"] = json.load(f).get("users", {})
            _users["mtime"] = m
            _ok_cache.clear()
        return _users["data"]


# Alle Schreiber (Login-Zeitstempel, Admin, Passwort-Link) lesen und schreiben users.json unter dieser Sperre,
# frisch von der Platte – sonst ueberschreibt ein Login eine gerade gesetzte Sperre mit einem alten Stand.
WRITE_LOCK = threading.RLock()


def read_disk():
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f).get("users", {})
    except FileNotFoundError:
        return {}


# Einstellungen je Login – nur diese Schluessel, nur bool
SETTINGS_DEFAULT = {"speed_warn": True, "lez_euro": 6, "lez_fuel": "diesel",
                    "critair_vignette": False, "lez_registered": False}


def _is_bool(v):
    return isinstance(v, bool)


# Pruefung je Schluessel (bool ist Unterklasse von int: lez_euro lehnt bool ausdruecklich ab)
SETTINGS_SPEC = {
    "speed_warn": _is_bool,
    "lez_euro": lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 6,
    "lez_fuel": lambda v: v in ("diesel", "petrol", "gas", "electric"),
    "critair_vignette": _is_bool,
    "lez_registered": _is_bool,
}


def settings_valid(changes):
    return (isinstance(changes, dict) and bool(changes)
            and all(k in SETTINGS_SPEC and SETTINGS_SPEC[k](v) for k, v in changes.items()))


def settings(user):
    return {**SETTINGS_DEFAULT, **(users().get(user or "", {}).get("settings") or {})}


def set_settings(user, changes):
    """changes schon geprueft (settings_valid)."""
    with WRITE_LOCK:
        data = read_disk()
        u = data[user]
        data[user] = {**u, "settings": {**(u.get("settings") or {}), **changes}}
        save_users(data)
    return settings(user)


def save_users(data):
    import tempfile
    with WRITE_LOCK:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(USERS_FILE) or ".", suffix=".users")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"users": data}, f, indent=1, ensure_ascii=False, sort_keys=True)
        os.chmod(tmp, 0o600)   # enthaelt Hashes und TOTP-Geheimnisse – nur der App-User liest
        os.replace(tmp, USERS_FILE)
        with _lock:   # Cache sofort auf den neuen Stand (mtime-Aufloesung kann zu grob sein)
            _users["data"], _users["mtime"] = data, os.path.getmtime(USERS_FILE)
            _ok_cache.clear()


MAX_PW_BYTES = 72   # bcrypt-Grenze; bcrypt 5 wirft darueber ValueError


def _pwb(pw):
    """Passwort als Bytes fuer bcrypt.checkpw – auf 72 Byte gekuerzt wie fruehere bcrypt-Versionen (alte Hashes passen weiter).
    Ohne Kuerzen endete ein langes Passwort mit 500 statt 401 und verriet so, dass es den Login gibt."""
    return (pw or "").encode()[:MAX_PW_BYTES]


def hash_pw(pw):
    """Neues Passwort hashen – Laenge vorher mit admin.pw_problem pruefen; hier nur die harte Grenze."""
    b = (pw or "").encode()
    if len(b) > MAX_PW_BYTES:
        raise ValueError(f"Passwort zu lang (höchstens {MAX_PW_BYTES} Byte).")
    return bcrypt.hashpw(b, bcrypt.gensalt(rounds=10)).decode()


_DUMMY_HASH = []


def _burn(pw):
    """Gleich viel Rechenzeit wie eine echte Pruefung – sonst verraet die Antwortzeit, welche Logins es gibt (0.38.0)."""
    if not _DUMMY_HASH:
        _DUMMY_HASH.append(bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt(rounds=10)))
    bcrypt.checkpw(_pwb(pw or "x"), _DUMMY_HASH[0])


def check(user, pw, app):
    """True, wenn Benutzer/Passwort stimmen und der Benutzer diese App (ride/rig) darf."""
    u = users().get((user or "").strip().lower())
    if not u or u.get("locked") or app not in u.get("apps", []) or not pw:
        _burn(pw)
        return False
    ck = (user.lower(), hashlib.sha256(pw.encode()).hexdigest())
    if _ok_cache.get(ck, 0) > time.time():
        return True
    h = u["hash"].encode()
    if h.startswith(b"$2y$"):                     # htpasswd-Variante, identisch zu $2b$
        h = b"$2b$" + h[4:]
    ok = bcrypt.checkpw(_pwb(pw), h)
    if ok:
        _ok_cache[ck] = time.time() + 600
    return ok


def pending_ok(user, pw):
    """Selbst registriert, noch nicht freigeschaltet, Passwort stimmt? (nur dann verraet die Anmeldeseite den Status)"""
    u = users().get((user or "").strip().lower())
    if not u or not u.get("pending") or not pw:
        return False
    return bcrypt.checkpw(_pwb(pw), u["hash"].encode())


def _reserve(table, key, limit):
    """Pruefen UND den Versuch zaehlen in einem Schritt (unter _lock) -> True = gesperrt."""
    now = time.time()
    with _lock:
        lst = [t for t in table.get(key, []) if now - t < FAIL_WINDOW]
        if len(lst) >= limit:
            table[key] = lst
            return True
        lst.append(now)
        table[key] = lst
        if len(table) > 2000:   # alte Eintraege abraeumen, sonst waechst das ewig
            for k in [k for k, v in table.items() if not v or now - v[-1] >= FAIL_WINDOW]:
                del table[k]
        return False


def _release(table, key):
    with _lock:
        lst = table.get(key)
        if lst:
            lst.pop()


def throttled(ip):
    """Seit 0.46.1 prueft UND reserviert das einen Versuch: vorher wurde erst nach dem (langsamen) Pruefen gezaehlt,
    gleichzeitige Anfragen liefen alle an der Bremse vorbei (300 parallele 2FA-Codes ohne 429). Danach: Erfolg ->
    release(ip), Fehlschlag -> failed(ip) (der Versuch bleibt gezaehlt)."""
    return _reserve(_fails, ip, MAX_FAILS)


def failed(ip):
    """Fehlschlag nach throttled(): der reservierte Versuch bleibt stehen – nichts weiter zu tun."""


def release(ip):
    """Erfolg nach throttled(): Reservierung zurueckgeben (zaehlt nicht als Fehlversuch)."""
    _release(_fails, ip)


MAX_USER_FAILS = 5   # 2FA-Codes je Login in FAIL_WINDOW – unabhaengig von der IP (Angreifer mit vielen IPs)
_user_fails = {}


def user_throttled(name):
    return _reserve(_user_fails, name, MAX_USER_FAILS)


def user_release(name):
    _release(_user_fails, name)


# ---------------------------------------------------------------- Cookie
# Aufbau: base64("user|app|exp|sid|sig"); sid = Zufalls-ID dieser einen Anmeldung.
# Abmelden setzt die sid auf die Sperrliste (data/revoked.json, bis zum Ablauf) – ein kopiertes Cookie ist danach
# wertlos. "Ueberall abmelden" zaehlt sess_gen im Login hoch, das steckt in der Signatur aller Cookies.
# Alte Cookies ohne sid bleiben bis zu ihrem Ablauf gueltig (gleiche Signatur wie frueher).
def _sig(user, app, exp, sid=""):
    """HMAC ueber Benutzer, App, Ablauf, Sitzungs-ID UND den aktuellen Passwort-Hash: neues Passwort
    (users.py passwd) oder geloeschter Benutzer machen alle alten Cookies ungueltig."""
    u = users().get(user, {})
    payload = f"{user}|{app}|{exp}|{u.get('hash', '')}"
    if sid:
        payload += f"|{sid}"
    if u.get("sess_gen"):
        payload += f"|g{u['sess_gen']}"
    return hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()


def make_cookie(user, app, remember):
    exp = int(time.time() + (REMEMBER_DAYS * 86400 if remember else SESSION_HOURS * 3600))
    sid = secrets.token_hex(8)
    sig = _sig(user, app, exp, sid)
    return base64.urlsafe_b64encode(f"{user}|{app}|{exp}|{sid}|{sig}".encode()).decode(), exp


def _parse(value):
    """-> (user, app, exp, sid, sig) oder None"""
    try:
        parts = base64.urlsafe_b64decode((value or "").encode()).decode().split("|")
    except Exception:  # noqa: BLE001 – beliebiger Muell im Cookie = nicht angemeldet
        return None
    if len(parts) == 4:
        user, app, exp, sig = parts
        sid = ""
    elif len(parts) == 5:
        user, app, exp, sid, sig = parts
    else:
        return None
    if not exp.isdigit():
        return None
    return user, app, exp, sid, sig


def read_cookie(value, app):
    """-> Benutzername oder None (falsch signiert, abgelaufen, abgemeldet, andere App, Benutzer geloescht)."""
    p = _parse(value)
    if not p:
        return None
    user, c_app, exp, sid, sig = p
    good = _sig(user, c_app, exp, sid)
    if not hmac.compare_digest(sig, good) or c_app != app or int(exp) < time.time():
        return None
    if sid and sid in _revoked():
        return None
    u = users().get(user)
    return user if u and not u.get("locked") and app in u.get("apps", []) else None


_rev = {"mtime": None, "data": {}}


def _revoked_file():
    return os.path.join(os.path.dirname(USERS_FILE), "revoked.json")


def _revoked():
    """Gesperrte Sitzungs-IDs {sid: ablauf}, bei Aenderung neu gelesen."""
    path = _revoked_file()
    try:
        m = os.path.getmtime(path)
    except OSError:
        return {}
    with _lock:
        if m != _rev["mtime"]:
            try:
                with open(path, encoding="utf-8") as f:
                    _rev["data"] = json.load(f)
            except (OSError, ValueError):
                _rev["data"] = {}
            _rev["mtime"] = m
        return _rev["data"]


def revoke(value):
    """Abmelden: diese eine Sitzung serverseitig ungueltig machen (alte Cookies ohne sid: nichts zu tun)."""
    p = _parse(value)
    if not p or not p[3]:
        return False
    sid, exp, now = p[3], int(p[2]), time.time()
    import tempfile
    with WRITE_LOCK:
        data = {k: v for k, v in dict(_revoked()).items() if v > now}
        data[sid] = exp
        path = _revoked_file()
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".revoked")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        with _lock:
            _rev["data"], _rev["mtime"] = data, os.path.getmtime(path)
    return True


def logout_everywhere(user):
    """Alle Sitzungen dieses Logins beenden (alle Geraete) – ohne Passwortwechsel."""
    with WRITE_LOCK:
        data = read_disk()
        if user not in data:
            return False
        data[user] = {**data[user], "sess_gen": int(data[user].get("sess_gen") or 0) + 1}
        save_users(data)
    return True


def basic_user(header, app):
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        user, pw = base64.b64decode(header[6:]).decode("utf-8", "replace").split(":", 1)
    except Exception:  # noqa: BLE001 – beliebiger Muell im Header = nicht angemeldet
        return None
    return user.strip().lower() if check(user, pw, app) else None


# ---------------------------------------------------------------- Rollen, CSRF, letzte Anmeldung
CSRF_COOKIE = "ra_csrf"


def is_admin(user):
    """Voll- oder Ride-Admin (beide mit 2FA); was er darf, sagt admin_scope."""
    return bool(users().get(user or "", {}).get("admin"))


def admin_scope(u):
    """users.json-Eintrag -> "all" (Oberadmin, admin: true), {"apps", "groups"} (Gruppen-Admin, seit v0.14) oder None.
    Das "ride" aus v0.13.2 gilt als Ride-Admin ohne Gruppe."""
    v = (u or {}).get("admin")
    if v is True:
        return "all"
    if v == "ride":
        v = {"apps": ["ride"]}
    if isinstance(v, dict):
        return {"apps": [a for a in ("ride", "rig") if a in (v.get("apps") or [])],
                "groups": [str(g) for g in (v.get("groups") or [])]}
    return None


def new_csrf():
    return secrets.token_urlsafe(24)


def same_origin(request, require=False):
    """Origin (sonst Referer) zeigt auf die eigene Domain? Ohne beide Header: ok, ausser require=True."""
    from urllib.parse import urlsplit
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").lower()
    src = request.headers.get("origin")
    if src and src != "null":
        return src.lower().split("://", 1)[-1].rstrip("/") == host
    ref = request.headers.get("referer")
    if ref:
        return urlsplit(ref).netloc.lower() == host
    return not require


def csrf_ok(request):
    """Double-Submit: Header X-RA-CSRF == Cookie ra_csrf, und Origin (falls gesendet) = eigene Domain."""
    tok = request.cookies.get(CSRF_COOKIE, "")
    if not tok or not hmac.compare_digest(tok, request.headers.get("x-ra-csrf", "")):
        return False
    return same_origin(request)


def touch_login(user, force=False):
    """Letzte Anmeldung merken – hoechstens alle 10 Minuten schreiben (weniger Schreiblast auf users.json)."""
    with WRITE_LOCK:
        data = read_disk()
        u = data.get(user)
        if not u:
            return
        last = u.get("last_login")
        now = time.time()
        if not force and last and now - time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%S")) < 600:
            return
        data[user] = {**u, "last_login": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))}
        save_users(data)
