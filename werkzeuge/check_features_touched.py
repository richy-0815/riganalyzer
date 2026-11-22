#!/usr/bin/env python3
"""CI-Schritt: Neue Funktion in releases.json => features.json muss mitgezogen werden.

Aufruf: check_features_touched.py BASE HEAD
Nur Standardbibliothek. Exit 1, wenn ein neuer Versionseintrag einen Punkt der Art
"neu" hat, features.json zwischen BASE und HEAD aber nicht geaendert wurde.
"""
import json
import subprocess
import sys

RELEASES = "app/releases.json"
FEATURES = "app/features.json"


def new_feature_version(old_releases, new_releases):
    """Version des ersten in new neuen Eintrags mit einem Punkt der Art 'neu', sonst None.
    (Auch genutzt von ciutils/handbuch/check.py --touched.)"""
    alt = {r.get("version") for r in old_releases}
    for r in new_releases:
        if r.get("version") in alt:
            continue
        if any(it and it[0] == "neu" for it in r.get("items", [])):
            return r.get("version")
    return None


def needs_features(old_releases, new_releases):
    """True, wenn ein in new neuer Versionseintrag einen Punkt der Art 'neu' hat."""
    return new_feature_version(old_releases, new_releases) is not None


def _git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout  # noqa: S603,S607 – nur git, Argumente aus dem Skript


def _releases_at(rev):
    try:
        return json.loads(_git("show", f"{rev}:{RELEASES}"))
    except subprocess.CalledProcessError:
        return []  # Datei gab es dort noch nicht


def main(argv):
    if len(argv) != 3:
        print("Aufruf: check_features_touched.py BASE HEAD", file=sys.stderr)
        return 2
    base, head = argv[1], argv[2]
    old, new = _releases_at(base), _releases_at(head)
    version = new_feature_version(old, new)
    if version is None:
        print("Keine neue Funktion in releases.json - nichts zu pruefen.")
        return 0
    if FEATURES in _git("diff", "--name-only", base, head).split():
        print(f"Neue Funktion (v{version}) und features.json geaendert - ok.")
        return 0
    print(f"Neue Funktion in releases.json (v{version}), aber features.json nicht geändert "
          "– bitte die Funktionsliste ergänzen.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
