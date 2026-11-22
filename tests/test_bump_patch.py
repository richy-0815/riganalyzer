"""bump_patch.py: Patch-Version hochzaehlen + Eintrag vorn in releases.json."""
import importlib.util
import json
from pathlib import Path

import pytest

_P = Path(__file__).resolve().parents[1] / "werkzeuge" / "bump_patch.py"
_spec = importlib.util.spec_from_file_location("bump_patch", _P)
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)

MAIN = 'import os\n\nVERSION = "0.35.5"  # Stand\nX = 1\n'
OLD = [{"version": "0.35.5", "date": "2026-11-06", "app": "beide", "title": "alt",
        "public": False, "items": [["fix", "Ümlaut", "both"]]}]


def make_repo(tmp_path, main=MAIN):
    app = tmp_path / "app"
    app.mkdir(parents=True)
    (app / "main.py").write_text(main, encoding="utf-8")
    (app / "releases.json").write_text(json.dumps(OLD, indent=1, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    return tmp_path, app


def test_bump_version_und_eintrag(tmp_path):
    root, app = make_repo(tmp_path)
    assert bp.bump(root, "Texte geprueft", today="2026-12-01") == "0.35.6"
    assert 'VERSION = "0.35.6"  # Stand' in (app / "main.py").read_text(encoding="utf-8")
    raw = (app / "releases.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data[1:] == OLD
    assert data[0] == {"version": "0.35.6", "date": "2026-12-01", "app": "beide",
                       "title": "Texte aktualisiert", "public": False,
                       "items": [["besser", "Texte geprueft", "both"]]}
    assert raw.endswith("]\n") and "Ümlaut" in raw   # ensure_ascii=False, Newline am Ende


def test_zweistellig_und_ohne_versionszeile(tmp_path):
    root, app = make_repo(tmp_path, 'VERSION = "1.2.9"\n')
    assert bp.bump(root, "x", today="2026-12-01") == "1.2.10"
    root2, _ = make_repo(tmp_path / "b", "nichts = 1\n")
    with pytest.raises(SystemExit):
        bp.bump(root2, "x", today="2026-12-01")
    # nichts veraendert
    assert json.loads((root2 / "app/releases.json").read_text()) == OLD
