"""Phase 2 der Trennung (seit 0.47.0): Datenordner aufteilen (werkzeuge/trennen.py) und Einzel-App-Container (RA_APP)."""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "werkzeuge"))
import trennen  # noqa: E402


def _w(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


def _r(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def alt(tmp_path):
    a = tmp_path / "alt"
    _w(str(a / "users.json"), {"users": {
        "rita": {"hash": "h1", "apps": ["ride"]},
        "otto": {"hash": "h2", "apps": ["rig"]},
        "bofh": {"hash": "h3", "apps": ["ride", "rig"], "admin": True, "totp_secret": "GEHEIM"}}})
    _w(str(a / "session.key"), "alt")
    _w(str(a / "reset.json"), {})
    _w(str(a / "osm" / "europa.sqlite"), "gross")
    _w(str(a / "trips" / "otto" / "t1.json"), {"name": "Tour"})
    _w(str(a / "shares.json"), {"shares": []})
    _w(str(a / "groups.json"), {"groups": {}})
    for u in ("rita", "otto", "bofh"):
        _w(str(a / "prefs" / f"{u}.json"), {"x": u})
    _w(str(a / "activity.json"), {"logins": {"rita": [1], "otto": [2], "bofh": [3]}, "seen": {}, "navi": {"otto": 1}})
    _w(str(a / "mailchange.json"), {"pending": {"rita": {}}, "sent": {}})
    _w(str(a / "feedback.json"), [{"user": "rita", "app": "ride"}, {"user": "otto", "app": "rig"}])
    _w(str(a / "events" / "2026-11-13.jsonl"),
       '{"kind": "analyse", "app": "ride"}\n{"kind": "planung", "app": "rig"}\n{"kind": "fehler"}\n')
    _w(str(a / "usage" / "2026-11.json"), {"since": "x", "users": {"rita": {"app": "ride"}, "otto": {"app": "rig"},
                                                                    "bofh": {"app": "rig"}}})
    return a


def test_aufteilen(alt, tmp_path):
    neu = {"ride": str(tmp_path / "ride"), "rig": str(tmp_path / "rig")}
    trennen.run(str(alt), neu, log=lambda *_: None)
    ride, rig = (_r(os.path.join(neu[a], "users.json"))["users"] for a in ("ride", "rig"))
    assert set(ride) == {"rita", "bofh"} and set(rig) == {"otto", "bofh"}
    assert ride["bofh"]["apps"] == ["ride"] and rig["bofh"]["apps"] == ["rig"]
    assert ride["bofh"]["hash"] == rig["bofh"]["hash"] == "h3"
    assert ride["bofh"]["totp_secret"] == rig["bofh"]["totp_secret"] == "GEHEIM"
    # nie mitnehmen: Sitzungsschluessel, Passwort-Links; gemeinsame Kartendaten bleiben draussen (nur lesend eingebunden)
    for a in neu.values():
        assert not os.path.exists(os.path.join(a, "session.key"))
        assert not os.path.exists(os.path.join(a, "reset.json"))
        assert not os.path.exists(os.path.join(a, "osm"))
        assert os.path.exists(os.path.join(a, "groups.json"))
    assert os.path.exists(os.path.join(neu["rig"], "trips", "otto", "t1.json"))
    assert not os.path.exists(os.path.join(neu["ride"], "trips"))
    assert not os.path.exists(os.path.join(neu["ride"], "shares.json"))
    assert sorted(os.listdir(os.path.join(neu["ride"], "prefs"))) == ["bofh.json", "rita.json"]
    act_ride, act_rig = (_r(os.path.join(neu[a], "activity.json")) for a in ("ride", "rig"))
    assert set(act_ride["logins"]) == {"rita", "bofh"} and "navi" not in act_ride and act_rig["navi"] == {"otto": 1}
    assert _r(os.path.join(neu["rig"], "mailchange.json"))["pending"] == {}
    assert [x["user"] for x in _r(os.path.join(neu["ride"], "feedback.json"))] == ["rita"]
    with open(os.path.join(neu["ride"], "events", "2026-11-13.jsonl"), encoding="utf-8") as f:
        kinds = [json.loads(x)["kind"] for x in f]
    assert kinds == ["analyse", "fehler"]   # Ereignisse ohne App (Fehler) in beiden Apps
    assert set(_r(os.path.join(neu["ride"], "usage", "2026-11.json"))["users"]) == {"rita"}
    assert set(_r(os.path.join(neu["rig"], "usage", "2026-11.json"))["users"]) == {"otto", "bofh"}
    # der alte Ordner bleibt unberuehrt (Rueckweg)
    assert _r(str(alt / "users.json"))["users"]["bofh"]["apps"] == ["ride", "rig"]
    assert (alt / "session.key").exists()


def test_probelauf_schreibt_nichts(alt, tmp_path):
    neu = {"ride": str(tmp_path / "ride"), "rig": str(tmp_path / "rig")}
    stat = trennen.run(str(alt), neu, probe=True, log=lambda *_: None)
    assert stat["ride"]["Logins"] == 2 and stat["rig"]["Logins"] == 2
    assert not os.path.exists(neu["ride"]) and not os.path.exists(neu["rig"])


def test_unbekannter_eintrag_bricht_ab(alt, tmp_path):
    _w(str(alt / "neu-und-unbekannt.json"), {})
    with pytest.raises(trennen.Abbruch, match="Unbekannte"):
        trennen.run(str(alt), {"ride": str(tmp_path / "r"), "rig": str(tmp_path / "g")}, probe=True, log=lambda *_: None)


def test_ziel_nicht_leer_bricht_ab(alt, tmp_path):
    _w(str(tmp_path / "ride" / "users.json"), {})
    with pytest.raises(trennen.Abbruch, match="nicht leer"):
        trennen.run(str(alt), {"ride": str(tmp_path / "ride"), "rig": str(tmp_path / "rig")}, log=lambda *_: None)


def test_alle_datenordner_der_app_sind_zugeordnet():
    """Neue Datei im Datenordner? Dann hier und in trennen.py zuordnen (sonst bricht die Migration ab)."""
    import re
    app = os.path.join(ROOT, "app")
    names = set()
    for f in os.listdir(app):
        if f.endswith(".py"):
            src = "\n".join(x for x in open(os.path.join(app, f), encoding="utf-8") if "os.path.join" in x)
            names |= set(re.findall(r'(?:DATA_DIR|_data\(\)|data)["\'\)]?\s*(?:,\s*"/data"\))?,\s*"([a-z][a-z0-9_.-]+)"', src))
    names -= {"osm", "dem", "shops"}
    assert names, "Muster findet keine Datenpfade mehr – Test anpassen"
    unknown = {n for n in names if n not in trennen.KNOWN}
    assert not unknown, f"nicht in trennen.py zugeordnet: {sorted(unknown)}"


# ---------------------------------------------------------------- Einzel-App-Container (RA_APP)
_PROBE = r"""
import os, sys, json
sys.path.insert(0, sys.argv[1])
import main, hooks, web
from fastapi.testclient import TestClient
c = TestClient(main.app, base_url="https://" + sys.argv[2])
print(json.dumps({"typ": type(main.app).__name__, "ext": hooks.extensions(), "apps": web.served_apps(),
                  "title": c.get("/login").text.split("<title>")[1].split("</title>")[0],
                  "demos": sorted({r for _, r in main._demo_list()})}))
"""


@pytest.mark.parametrize("app,host,rig", [("ride", "riganalyzer.petrick.top", False), ("rig", "rideanalyzer.petrick.top", True)])
def test_einzel_app_container(tmp_path, app, host, rig):
    env = {**os.environ, "RA_APP": app, "DATA_DIR": str(tmp_path), "REPORT": "0", "LEZ_REFRESH": "0",
           "FRCAMPS_REFRESH": "0", "DEMO_WARM": "0", "OSM_AUTOBUILD": "0"}
    env.pop("RA_EXTENSIONS", None)
    out = subprocess.run([sys.executable, "-c", _PROBE, os.path.join(ROOT, "app"), host], env=env, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["typ"] == "FastAPI" and r["apps"] == [app]
    assert r["ext"] == (["rig_ext"] if rig else [])          # Ride laedt keine Rig-Erweiterung
    assert r["title"].startswith("RigAnalyzer" if rig else "RideAnalyzer")   # Profil schlaegt den Host
    assert set(r["demos"]) <= {rig}          # Export: Rig ohne eigene Beispiele
