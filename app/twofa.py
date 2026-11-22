"""Zweiter Faktor fuer Admins (seit v0.9.3): Code-App (TOTP, RFC 6238) + 8 Notfall-Codes.

- Schluessel in users.json (totp_secret), Notfall-Codes nur als SHA-256 (totp_backup), zuletzt genutzter
  Zeitschritt (totp_last) gegen doppelte Verwendung eines Codes.
- Admin-Sitzung = Cookie ra_mfa (HMAC ueber Benutzer, Ablauf und Schluessel): 2FA aus -> Cookie ungueltig.
- Anmeldung in zwei Schritten: nach dem Passwort ein kurzes Zwischen-Cookie ra_pend (5 min), dann der Code.
"""

import base64
import hashlib
import hmac
import secrets
import time

import pyotp
import segno

import auth

MFA_COOKIE, PEND_COOKIE = "ra_mfa", "ra_pend"
ISSUER = "RigAnalyzer/RideAnalyzer"
PEND_TTL = 300
BACKUP_COUNT = 8


class Invalid(Exception):
    pass


def _h(code):
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def _sig(*parts):
    return hmac.new(auth._key(), "|".join(map(str, parts)).encode(), hashlib.sha256).hexdigest()


def enabled(user):
    return bool(auth.users().get(user or "", {}).get("totp_secret"))


# ---------------------------------------------------------------- Einrichten
def begin_setup(user):
    """Vorlaeufiger Schluessel (bleibt bis zur Bestaetigung gleich, damit ein Neuladen den QR-Code nicht aendert)."""
    with auth.WRITE_LOCK:
        data = auth.read_disk()
        u = data[user]
        if not u.get("totp_pending"):
            data[user] = {**u, "totp_pending": pyotp.random_base32()}
            auth.save_users(data)
        return data[user]["totp_pending"]


def qr_svg(user, secret):
    uri = pyotp.TOTP(secret).provisioning_uri(name=user, issuer_name=ISSUER)
    return segno.make(uri, error="m").svg_inline(scale=5, dark="#1b2421", light="#ffffff")


def confirm_setup(user, code):
    """Code aus der App bestaetigt den vorlaeufigen Schluessel -> Notfall-Codes (nur jetzt im Klartext)."""
    with auth.WRITE_LOCK:
        data = auth.read_disk()
        u = data[user]
        secret = u.get("totp_pending")
        if not secret or not pyotp.TOTP(secret).verify((code or "").strip(), valid_window=1):
            raise Invalid()
        codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(BACKUP_COUNT)]
        u = {k: v for k, v in u.items() if k != "totp_pending"}
        u.update(totp_secret=secret, totp_backup=[_h(c) for c in codes], totp_last=int(time.time() // 30))
        data[user] = u
        auth.save_users(data)
    return codes


def disable(user):
    with auth.WRITE_LOCK:
        data = auth.read_disk()
        if user in data:
            data[user] = {k: v for k, v in data[user].items() if not k.startswith("totp_")}
            auth.save_users(data)


# ---------------------------------------------------------------- Pruefen
def verify(user, code):
    """6-stelliger Code (jeder Zeitschritt nur einmal) oder Notfall-Code (jeder nur einmal)."""
    code = (code or "").strip().replace(" ", "")
    with auth.WRITE_LOCK:
        data = auth.read_disk()
        u = data.get(user) or {}
        secret = u.get("totp_secret")
        if not secret:
            return False
        if code.isdigit() and len(code) == 6:
            totp = pyotp.TOTP(secret)
            now = int(time.time() // 30)
            for step in (now - 1, now, now + 1):
                if step > u.get("totp_last", 0) and hmac.compare_digest(totp.at(step * 30), code):
                    data[user] = {**u, "totp_last": step}
                    auth.save_users(data)
                    return True
            return False
        h = _h(code)
        if h in u.get("totp_backup", []):
            data[user] = {**u, "totp_backup": [b for b in u["totp_backup"] if b != h]}
            auth.save_users(data)
            return True
    return False


# ---------------------------------------------------------------- Cookies
def make_mfa(user, exp):
    secret = auth.users().get(user, {}).get("totp_secret", "")
    return base64.urlsafe_b64encode(f"{user}|{exp}|{_sig('mfa', user, exp, secret)}".encode()).decode()


def mfa_ok(request, user):
    try:
        u, exp, sig = base64.urlsafe_b64decode(request.cookies.get(MFA_COOKIE, "").encode()).decode().split("|")
    except Exception:  # noqa: BLE001 – beliebiger Muell im Cookie = nicht bestaetigt
        return False
    secret = auth.users().get(user or "", {}).get("totp_secret")
    return (bool(secret) and u == user and int(exp) > time.time()
            and hmac.compare_digest(sig, _sig("mfa", u, exp, secret)))


def make_pending(user, app, remember):
    exp = int(time.time()) + PEND_TTL
    return base64.urlsafe_b64encode(f"{user}|{app}|{int(remember)}|{exp}|{_sig('pend', user, app, int(remember), exp)}"
                                    .encode()).decode()


def read_pending(value):
    """-> (user, app, remember) oder None."""
    try:
        user, app, rem, exp, sig = base64.urlsafe_b64decode((value or "").encode()).decode().split("|")
    except Exception:  # noqa: BLE001 – beliebiger Muell im Cookie = ungueltig
        return None
    if int(exp) < time.time() or not hmac.compare_digest(sig, _sig("pend", user, app, rem, exp)):
        return None
    return user, app, rem == "1"
