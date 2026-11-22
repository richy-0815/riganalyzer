#!/usr/bin/env python3
"""Trennung Ride/Rig, Phase 2: einen gemeinsamen Datenordner in zwei aufteilen (seit 0.47.0).

    python3 werkzeuge/trennen.py ALT NEU_RIDE NEU_RIG [--probe]

- ALT wird nur gelesen. NEU_RIDE und NEU_RIG muessen leer sein oder fehlen (kein Ueberschreiben).
- --probe: nichts schreiben, nur auflisten, was wohin ginge (plus Zaehlungen).
- Gemeinsame Kartendaten (osm, dem, shops) werden NICHT kopiert – die Container binden sie nur lesend ein.
- session.key, offene Passwort-Links und gesperrte Sitzungen werden nicht uebernommen: jede App bekommt einen neuen
  Schluessel, alle melden sich einmal neu an (RigNavi meldet sich per Basic-Auth an und ist nicht betroffen).
- Logins mit beiden Apps werden zu je einem Login pro App (gleicher Passwort-Hash, gleicher 2FA-Schluessel –
  Beschluss 13.11.2026), danach unabhaengig.
- Unbekannte Eintraege im Datenordner brechen ab: lieber anhalten als still etwas verlieren.
"""

import argparse
import json
import os
import shutil
import sys

APPS = ("ride", "rig")
SHARED = {"osm", "dem", "dem-prod", "shops", "osm-build"}   # nur lesend eingebunden bzw. Bau-Arbeitsordner (baut der Baurechner)
DROP = {"session.key", "reset.json", "revoked.json", "qa-postfach.json"}
BOTH = {"admin-log.json", "build-stats.json", "devcosts.json", "geofabrik-index.json", "host-info.json",
        "region-sizes.json", "regions-extra.json", "platz.json", "platz-gestern.json", "groups.json",
        "lez", "cache", "demo-cache", "result-cache", "costs"}
RIG_ONLY = {"trips", "packages", "package-timing.json", "shares.json", "reviews.sqlite", "frcamps.json", "frcamps", "app"}
SPLIT = {"users.json", "prefs", "activity.json", "mailchange.json", "feedback.json", "events", "usage"}
KNOWN = SHARED | DROP | BOTH | RIG_ONLY | SPLIT


class Abbruch(Exception):
    pass


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(path, data, probe):
    if probe:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _copy(src, dst, probe):
    if probe:
        return
    if os.path.isdir(src):
        shutil.copytree(src, dst, symlinks=True)
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def split_users(users):
    """users.json -> {app: users.json}; jedes Login nur mit dieser App in "apps"."""
    out = {a: {**{k: v for k, v in users.items() if k != "users"}, "users": {}} for a in APPS}
    for name, u in users.get("users", {}).items():
        for a in APPS:
            if a in (u.get("apps") or []):
                out[a]["users"][name] = {**u, "apps": [a]}
    return out


def members(users_by_app):
    return {a: set(users_by_app[a]["users"]) for a in APPS}


def run(alt, neu, probe=False, log=print):
    """neu = {"ride": Pfad, "rig": Pfad}. -> Zaehlungen je App."""
    entries = sorted(os.listdir(alt))
    unknown = [e for e in entries if e not in KNOWN]
    if unknown:
        raise Abbruch(f"Unbekannte Eintraege in {alt}: {unknown} – erst in trennen.py zuordnen")
    for a in APPS:
        if os.path.exists(neu[a]) and os.listdir(neu[a]):
            raise Abbruch(f"{neu[a]} ist nicht leer")
    stat = {a: {} for a in APPS}

    def note(a, what, n=1):
        stat[a][what] = stat[a].get(what, 0) + n

    users = split_users(_load(os.path.join(alt, "users.json")))
    mem = members(users)
    for a in APPS:
        _dump(os.path.join(neu[a], "users.json"), users[a], probe)
        note(a, "Logins", len(mem[a]))

    for e in entries:
        src = os.path.join(alt, e)
        if e in SHARED or e in DROP or e == "users.json":
            continue
        if e in BOTH:
            for a in APPS:
                _copy(src, os.path.join(neu[a], e), probe)
                note(a, e)
        elif e in RIG_ONLY:
            _copy(src, os.path.join(neu["rig"], e), probe)
            note("rig", e)
        elif e == "prefs":
            for f in sorted(os.listdir(src)):
                for a in APPS:
                    if f.endswith(".json") and f[:-5] in mem[a]:
                        _copy(os.path.join(src, f), os.path.join(neu[a], "prefs", f), probe)
                        note(a, "Einstellungen")
        elif e == "activity.json":
            act = _load(src)
            for a in APPS:
                part = {k: ({u: v for u, v in d.items() if u in mem[a]} if isinstance(d, dict) else d)
                        for k, d in act.items() if not (k == "navi" and a != "rig")}
                _dump(os.path.join(neu[a], e), part, probe)
        elif e == "mailchange.json":
            mc = _load(src)
            for a in APPS:
                part = {k: ({u: v for u, v in d.items() if u in mem[a]} if isinstance(d, dict) else d) for k, d in mc.items()}
                _dump(os.path.join(neu[a], e), part, probe)
        elif e == "feedback.json":
            fb = _load(src)
            for a in APPS:
                part = [x for x in fb if x.get("app") in (a, None)]
                _dump(os.path.join(neu[a], e), part, probe)
                note(a, "Rückmeldungen", len(part))
        elif e == "events":
            for f in sorted(os.listdir(src)):
                lines = {a: [] for a in APPS}
                with open(os.path.join(src, f), encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            app = json.loads(line).get("app")
                        except ValueError:
                            app = None
                        for a in APPS:
                            if app in (a, None):
                                lines[a].append(line if line.endswith("\n") else line + "\n")
                for a in APPS:
                    note(a, "Ereignisse", len(lines[a]))
                    if not probe and lines[a]:
                        os.makedirs(os.path.join(neu[a], "events"), exist_ok=True)
                        with open(os.path.join(neu[a], "events", f), "w", encoding="utf-8") as fh:
                            fh.writelines(lines[a])
        elif e == "usage":
            for f in sorted(os.listdir(src)):
                data = _load(os.path.join(src, f))
                for a in APPS:
                    # Verbrauch je Login steht unter der zuletzt benutzten App ("app"); Logins nur dieser App kommen mit
                    part = {**data, "users": {u: v for u, v in data.get("users", {}).items()
                                              if v.get("app") == a or (v.get("app") is None and u in mem[a])}}
                    _dump(os.path.join(neu[a], "usage", f), part, probe)
                    note(a, "Verbrauch", len(part["users"]))
    for a in APPS:
        log(f"{a}: " + ", ".join(f"{k} {v}" for k, v in sorted(stat[a].items())))
    return stat


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("alt")
    p.add_argument("neu_ride")
    p.add_argument("neu_rig")
    p.add_argument("--probe", action="store_true", help="nichts schreiben, nur zeigen")
    a = p.parse_args(argv)
    try:
        run(a.alt, {"ride": a.neu_ride, "rig": a.neu_rig}, probe=a.probe)
    except Abbruch as e:
        print(f"Abbruch: {e}", file=sys.stderr)
        return 2
    print("Probelauf – nichts geschrieben." if a.probe else "Fertig. Jetzt die Rechte pruefen (Container-User 10001).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
