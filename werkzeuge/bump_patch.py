#!/usr/bin/env python3
"""Patch-Version in app/main.py hochzaehlen und Eintrag vorn in app/releases.json setzen.

Aufruf (aus dem Repo-Root): python3 werkzeuge/bump_patch.py "<Text>"
Nur Standardbibliothek.
"""
import datetime
import json
import re
import sys
from pathlib import Path

APP = Path("app")
VERSION_RE = re.compile(r'^(VERSION = ")(\d+)\.(\d+)\.(\d+)(")', re.M)


def bump(root, text, today=None):
    root = Path(root)
    main = root / APP / "main.py"
    rel = root / APP / "releases.json"
    src = main.read_text(encoding="utf-8")
    m = VERSION_RE.search(src)
    if not m:
        sys.exit("bump_patch: keine VERSION-Zeile in app/main.py gefunden")
    version = f"{m.group(2)}.{m.group(3)}.{int(m.group(4)) + 1}"
    data = json.loads(rel.read_text(encoding="utf-8"))
    data.insert(0, {"version": version, "date": today or datetime.date.today().isoformat(),
                    "app": "beide", "title": "Texte aktualisiert", "public": False,
                    "items": [["besser", text, "both"]]})
    main.write_text(src[:m.start()] + f'{m.group(1)}{version}{m.group(5)}' + src[m.end():],
                    encoding="utf-8")
    rel.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return version


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        sys.exit('Aufruf: bump_patch.py "<Text>"')
    print(bump(".", sys.argv[1]))
