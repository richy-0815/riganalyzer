"""Logins verwalten (ohne Neustart, die App liest users.json bei Aenderung neu).

  docker exec rideanalyzer python users.py list
  docker exec rideanalyzer python users.py add NAME [ride|rig|beide]   # neues Passwort wird ausgegeben
  docker exec rideanalyzer python users.py passwd NAME                 # neues Passwort
  docker exec rideanalyzer python users.py del NAME
  docker exec rideanalyzer python users.py admin NAME an|aus           # Oberadmin-Recht (Seite /admin); Gruppen-Admins im Admin-Bereich
  docker exec rideanalyzer python users.py mail NAME ADRESSE           # Mail fuer „Passwort vergessen“
  docker exec rideanalyzer python users.py 2fa NAME aus                 # Notzugang: 2FA zuruecksetzen
  docker exec rideanalyzer python users.py frei NAME                    # selbst registrierten Login freischalten (ohne Mail)
"""

import secrets
import string
import sys

import auth


def _pw():
    a = string.ascii_letters + string.digits
    return "".join(secrets.choice(a) for _ in range(14))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, data = argv[1], dict(auth.users())
    if cmd == "list":
        for n, u in sorted(data.items()):
            print(f"{n:12s} {','.join(u.get('apps', [])):9s} {'admin' if u.get('admin') is True else 'gruppen-admin' if u.get('admin') else '':13s} "
                  f"{'WARTET' if u.get('pending') else 'GESPERRT' if u.get('locked') else '':8s} "
                  f"{'Startpw.' if u.get('pw_initial') else '':8s} {u.get('mail', '')}")
        return 0
    name = argv[2].strip().lower() if len(argv) > 2 else ""
    if not name or not name.isalnum():
        print("Name fehlt oder enthaelt Sonderzeichen.")
        return 1
    if cmd == "add":
        which = argv[3] if len(argv) > 3 else "beide"
        apps = {"ride": ["ride"], "rig": ["rig"]}.get(which, ["ride", "rig"])
        pw = _pw()
        data[name] = {"hash": auth.hash_pw(pw), "apps": apps, "pw_initial": True}
        auth.save_users(data)
        print(f"{name} / {pw}  ({', '.join(apps)})")
    elif cmd == "passwd":
        if name not in data:
            print("Unbekannt.")
            return 1
        pw = _pw()
        data[name] = {k: v for k, v in data[name].items() if k != "pw_changed"}
        data[name].update(hash=auth.hash_pw(pw), pw_initial=True)
        auth.save_users(data)
        print(f"{name} / {pw}")
    elif cmd == "2fa":
        if name not in data or len(argv) < 4 or argv[3] != "aus":
            print("Nur: 2fa NAME aus")
            return 1
        data[name] = {k: v for k, v in data[name].items() if not k.startswith("totp_")}
        auth.save_users(data)
        print(f"{name}: 2FA zurueckgesetzt – beim naechsten Oeffnen von /admin neu einrichten")
    elif cmd in ("admin", "mail"):
        if name not in data or len(argv) < 4:
            print("Unbekannt oder Wert fehlt.")
            return 1
        val = argv[3] in ("an", "ja", "1") if cmd == "admin" else argv[3].strip()
        data[name] = {**data[name], cmd: val}
        auth.save_users(data)
        if cmd == "mail":   # gilt sofort – offener Mailwechsel aus dem Konto ist damit hinfaellig
            import mailchange
            mailchange.drop(name)
        print(f"{name}: {cmd} = {val}")
    elif cmd == "frei":
        if name not in data or not data[name].get("pending"):
            print("Kein offener Antrag.")
            return 1
        data[name] = {k: v for k, v in data[name].items() if k not in ("pending", "note", "locked")}
        auth.save_users(data)
        print(f"{name} freigeschaltet")
    elif cmd == "del":
        data.pop(name, None)
        auth.save_users(data)
        import purge   # Daten mit wegraeumen (wie beim Loeschen im Admin), sonst erbt sie ein neuer gleichen Namens
        weg = purge.purge_user(name)
        print(f"{name} geloescht" + (f" (aufgeraeumt: {', '.join(weg)})" if weg else ""))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
