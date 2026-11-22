"""Seed der QA-Abnahmetest-Logins (nur STAGE=qa): docker exec <container> python e2e_seed.py

Legt e2e-ride / e2e-rig / e2e-beide / e2e-gruppe / e2e-admin und die Gruppe „E2E“ an, raeumt Reste der
Registrierungstests weg (e2eneu*: Selbst-Registrierung/Admin-Anlage erlauben nur [a-z0-9], kein Bindestrich;
dazu das alte e2e-neu-*) und fasst alle anderen Logins nicht an. Idempotent: ein noch passendes
Passwort-Hash bleibt unveraendert. Beide Admins (e2e-admin, e2e-gruppe) bekommen den TOTP-Schluessel –
Admins ohne 2FA kommen nur bis /2fa-einrichten. Passwort/TOTP-Schluessel kommen aus E2E_PASSWORD / E2E_TOTP_SECRET und
werden nie ausgegeben. Rueckmeldungen der Test-Logins (e2e-*, e2eneu*) loescht jeder Seed, sonst greift nach
10 Laeufen am Tag die Tagesgrenze je Login (feedback.PER_DAY)."""
import json
import os
import sys
import tempfile

import bcrypt

import auth
import feedback
import mailchange
import qapost

GID = "e2e"
GROUP_NAME = "E2E"
MAIL = "e2e@localhost"
LEFTOVER = ("e2e-neu-", "e2eneu")
TEST_USERS = ("e2e-", "e2eneu")      # Rueckmeldungen dieser Logins raeumt der Seed weg   # Registrierungs-/Anlagetests (e2e/test_konto.py, e2e/test_admin.py)


def _hash_ok(h, pw):
    try:
        return bcrypt.checkpw(pw.encode(), h.encode())
    except (ValueError, AttributeError):
        return False


def seed(users, groups, password, totp):
    users = {k: v for k, v in users.items() if not k.startswith(LEFTOVER)}
    # Gruppe „E2E“: vorhandene (per Name) weiterverwenden, sonst feste Kennung
    gid = next((g for g, v in groups.items() if v.get("name") == GROUP_NAME), GID)
    groups = {**groups, gid: {"name": GROUP_NAME}}
    want = {
        "e2e-ride": {"apps": ["ride"]},
        "e2e-rig": {"apps": ["rig"]},
        "e2e-beide": {"apps": ["ride", "rig"]},
        "e2e-gruppe": {"apps": ["ride", "rig"], "group": gid,
                       "admin": {"apps": ["ride", "rig"], "groups": [gid]}, "totp_secret": totp, "totp_backup": []},
        "e2e-admin": {"apps": ["ride", "rig"], "admin": True, "totp_secret": totp, "totp_backup": []},
    }
    for name, spec in want.items():
        old = users.get(name) or {}
        h = old["hash"] if _hash_ok(old.get("hash"), password) else auth.hash_pw(password)
        users[name] = {**{k: v for k, v in old.items() if k not in ("totp_secret", "totp_backup", "admin", "group")},
                       **spec, "hash": h, "mail": MAIL}
    return users, groups


def clean_feedback(items):
    """Rueckmeldungen ohne die der Test-Logins."""
    return [x for x in items if not str(x.get("user", "")).startswith(TEST_USERS)]


def _write_json(path, key, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".e2e")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({key: data}, f, indent=1, ensure_ascii=False, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main():
    if os.environ.get("STAGE") != "qa":
        print("e2e_seed: nur mit STAGE=qa (Prod bekommt keine Test-Accounts)", file=sys.stderr)
        sys.exit(2)
    pw, totp = os.environ.get("E2E_PASSWORD", ""), os.environ.get("E2E_TOTP_SECRET", "")
    if not pw or not totp:
        print("e2e_seed: E2E_PASSWORD und E2E_TOTP_SECRET muessen gesetzt sein", file=sys.stderr)
        sys.exit(2)
    gfile = os.path.join(os.path.dirname(auth.USERS_FILE) or ".", "groups.json")
    with auth.WRITE_LOCK:
        old_u = auth.read_disk()
        try:
            with open(gfile, encoding="utf-8") as f:
                old_g = json.load(f).get("groups", {})
        except FileNotFoundError:
            old_g = {}
        new_u, new_g = seed(json.loads(json.dumps(old_u)), dict(old_g), pw, totp)
        changed = (new_u, new_g) != (old_u, old_g)
        if new_g != old_g:
            _write_json(gfile, "groups", new_g)
        if new_u != old_u:
            auth.save_users(new_u)
    with feedback._lock:
        old_f = feedback._load()
        new_f = clean_feedback(old_f)
        if new_f != old_f:
            feedback._save(new_f)
            changed = True
    # Mailwechsel: sonst sammelt jeder QA-Lauf Zeitstempel der Mengenbremse (3/h) -> ab dem 4. Lauf 429
    if mailchange.forget(lambda n: n.startswith(TEST_USERS)):
        changed = True
    # QA-Postfach: jeder Deploy beginnt leer
    if qapost.clear():
        changed = True
    print("e2e_seed: geaendert" if changed else "e2e_seed: unveraendert")


if __name__ == "__main__":
    main()
