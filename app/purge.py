"""Daten eines geloeschten Logins wegraeumen (seit 0.46.1).

Vorher blieb alles am Namen haengen: wer denselben Namen spaeter neu bekam (Registrierung + Freischaltung), sah die
alten Touren samt privater Notiz, Navi-Pakete, Einstellungen und Login-Historie mit IPs.
- geloescht: Touren (mit Versionen/Analysen), Navi-Pakete, Einstellungen, Login-Historie/zuletzt gesehen,
  offene Einladungen (shares) und ein offener Mailwechsel
- umbenannt auf „<name> (gelöscht)“: Platz-Bewertungen, Rueckmeldungen, Verbrauchszaehler – fuer andere bzw. die
  Kostenuebersicht weiter sichtbar, aber keinem Login mehr zugeordnet (ein neuer „<name>“ kann sie weder sehen
  noch aendern)
Seit 0.46.4 raeumt der Kern nur seine eigenen Teile; Touren, Pakete, Einladungen und Bewertungen meldet die
Rig-Erweiterung ueber hooks.py an ("purge.user", siehe rig_ext.py).
"""

import logging
import os
import shutil
import sqlite3

log = logging.getLogger("purge")


def gone(name):
    return f"{name} (gelöscht)"


def _data():
    return os.environ.get("DATA_DIR", "/data")


def _rmtree(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
        return 1
    return 0


def purge_user(name):
    """-> Liste, was weg/umbenannt ist (fuers Admin-Protokoll). Fehler einzelner Teile halten den Rest nicht auf."""
    import activity
    import feedback
    import hooks
    import mailchange
    import usage

    name = (name or "").strip().lower()
    if not name or "/" in name or name.startswith("."):
        return []
    done = []

    def step(label, fn):
        try:
            if fn():
                done.append(label)
        except Exception:   # ein kaputter Teil darf das Loeschen der anderen nicht verhindern
            log.exception("Aufraeumen %s fuer %s", label, name)

    def prefs():
        p = os.path.join(_data(), "prefs", name + ".json")
        if os.path.isfile(p):
            os.remove(p)
            return True
        return False
    step("Einstellungen", prefs)

    def act():
        with activity._lock:
            hit = False
            for k in ("logins", "seen", "navi"):
                hit = activity._state[k].pop(name, None) is not None or hit
        return hit
    step("Login-Historie", act)
    step("Mailwechsel", lambda: mailchange.cancel(name))


    def fb():
        with feedback._lock:
            items = feedback._load()
            hit = False
            for x in items:
                if x.get("user") == name:
                    x["user"] = gone(name)
                    hit = True
            if hit:
                feedback._save(items)
        return hit
    step("Rückmeldungen", fb)

    def use():
        hit = False
        for ym in usage.months():
            def fn(data, ym=ym):
                nonlocal hit
                u = data.get("users", {})
                if name in u:
                    u[gone(name)] = u.pop(name)
                    hit = True
            usage._update(ym, fn)
        return hit
    step("Verbrauch", use)
    for fn in hooks.all_("purge.user"):   # App-Daten (Rig: Touren, Pakete, Einladungen, Bewertungen)
        fn(name, step)
    return done


def _safe_sql(fn):
    try:
        return fn()
    except sqlite3.Error:
        log.exception("Aufraeumen in der Datenbank")
        return False
