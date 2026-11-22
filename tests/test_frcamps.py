"""DATAtourisme-Plaetze (seit v0.24.0): Regionsdatei lesen, OSM-Plaetzen zuordnen, fehlende als Extra liefern."""
import json
import os

import pytest

import frcamps
from test_admin import basic, env  # noqa: F401  (Fixture env)

SAMPLE = open(os.path.join(os.path.dirname(__file__), "fixtures", "datatourisme-sample.csv"), encoding="utf-8").read()


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    items = frcamps.parse_csv(SAMPLE)
    f = tmp_path / "frcamps.json"
    f.write_text(json.dumps({"t": 1793000000, "items": items}))
    monkeypatch.setattr(frcamps, "FILE", str(f))
    frcamps._data.update(t=0.0, items=[], grid={})
    frcamps._load(force=True)
    yield items
    frcamps._data.update(t=0.0, items=[], grid={})


def test_parse_keeps_only_camps():
    items = frcamps.parse_csv(SAMPLE)
    by = {i["n"]: i for i in items}
    assert len(items) == 3 and "Vins et coquillages au Château de Calavon" not in by
    assert "Domaine de Lucena" not in by                              # dieser Eintrag ist eine Weinprobe, kein Platz
    roches = by["Camping Municipal Les Roches"]
    assert roches["k"] == "camping" and roches["s"] == 2 and roches["w"] == "https://www.lesrochesverdon.com/"
    assert "Sainte-Croix-du-Verdon" in roches["a"] and "161 emplacements" in roches["d"]
    manades = by["Aire de service pour camping-cars du camping des manades"]
    assert manades["k"] == "aire" and manades["r"].startswith("https://reservation.") and manades["w"].startswith("https://www.campinglesmanades")


def test_lookup_match_by_distance_and_name(loaded):
    pois = [("way/1", 43.7612, 6.1540, "Camping Les Roches"),          # ~40 m daneben
            ("node/2", 43.6550, 4.7080, "Camping des Manades"),       # ~640 m, aber gleicher Namensteil
            ("node/3", 43.6450, 4.8300, "Parking Nord")]              # ~650 m, anderer Name -> nein
    r = frcamps.lookup(pois, [])
    assert set(r["match"]) == {"way/1", "node/2"}
    assert r["match"]["way/1"]["s"] == 2 and r["source"].startswith("DATAtourisme")


def test_lookup_extras_along_route(loaded):
    line = [(43.6400, 4.8100, 100.0), (43.6400, 4.8300, 101.6)]      # an der „Aire publique“ vorbei
    r = frcamps.lookup([], line)
    names = [e["n"] for e in r["extra"]]
    assert names == ["Aire de camping-cars publique"]                # Service-Aire zaehlt nicht als Platz
    e = r["extra"][0]
    assert e["km"] == 101.6 and e["off"] < 600


def test_api(env, loaded):  # noqa: F811 – Fixture (Import oben)
    body = {"pois": [["way/1", 43.7612, 6.1540, "Les Roches"]], "line": [[43.64, 4.83, 5]]}
    r = env.post("/api/camps/fr", json=body, headers=basic("olaf"))
    assert r.status_code == 200 and "way/1" in r.json()["match"] and r.json()["extra"]
    assert env.post("/api/camps/fr", json={"pois": [["x", "a", 1, ""]]}, headers=basic("olaf")).status_code == 422


def test_refresh_isolated_runs_in_own_process(tmp_path, monkeypatch):
    """0.38.4: Der Wochenabruf (~400 MB CSV) parst in einem eigenen Prozess – im App-Prozess blockierte er sonst
    sekundenlang alles, Traefik hielt die App für tot (503). Danach lädt die App nur die fertige Datei neu."""
    items = frcamps.parse_csv(SAMPLE)
    f = tmp_path / "frcamps.json"
    monkeypatch.setattr(frcamps, "FILE", str(f))
    frcamps._data.update(t=0.0, items=[], grid={})
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        f.write_text(json.dumps({"t": 1793000000, "items": items}))   # das täte der Kindprozess
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(frcamps.subprocess, "run", fake_run)
    assert frcamps.refresh_isolated() == len(items)
    (cmd, kw), = calls
    assert cmd[0] == frcamps.sys.executable and "frcamps" in " ".join(cmd)
    assert kw.get("check") and kw.get("timeout") and kw["env"]["DATA_DIR"]
    assert len(frcamps._data["items"]) == len(items)                     # App-Prozess hat neu geladen
    frcamps._data.update(t=0.0, items=[], grid={})
