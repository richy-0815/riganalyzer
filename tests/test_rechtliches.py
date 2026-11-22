"""Impressum und Datenschutz (0.52.0): ohne Login erreichbar, je App getrennt, verlinkt, Fristen passend zum Code."""
import json
import subprocess
import sys

import pytest

import main
import rechtliches

RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"


@pytest.fixture
def client(data_dir):
    from fastapi.testclient import TestClient
    return lambda host: TestClient(main.app, base_url=host)


@pytest.mark.parametrize("host", [RIDE, RIG])
@pytest.mark.parametrize("path", ["/datenschutz", "/datenschutz.html", "/impressum", "/impressum.html"])
def test_public_without_login(client, host, path):
    r = client(host).get(path, follow_redirects=False)
    assert r.status_code == 200 and "lg-wide" in r.text


@pytest.mark.parametrize("host", [RIDE, RIG])
def test_linked_from_login_register_and_info(client, host):
    c = client(host)
    for path in ("/login", "/registrieren", "/info.html", "/passwort-vergessen"):
        t = c.get(path).text
        assert 'href="/datenschutz"' in t and 'href="/impressum"' in t, path
    assert "IP-Adresse" in c.get("/registrieren").text   # Hinweis direkt am Antrag (Art. 13 DSGVO)


def test_ride_and_rig_separated():
    ride, rig = rechtliches.datenschutz(False), rechtliches.datenschutz(True)
    for word in ("RigNavi", "Mapillary", "Gespann", "Fahrzeug-Profile", "OpenFreeMap"):
        assert word not in ride, word
    assert "RideAnalyzer" in ride and "RigAnalyzer" in rig and "Mapillary" in rig and "Standort aus RigNavi" in rig
    # Esri/Mapillary laufen ueber den Server – nicht mehr unter „Browser laedt direkt“
    for t in (ride, rig):
        direkt = t[t.index("Dienste, die euer Browser"):t.index("Dienste, die unser Server")]
        assert "Esri" not in direkt and "Mapillary" not in direkt and "Esri" in t
    assert "Motorrad" not in rig


def test_no_infrastructure_details():
    for rig in (False, True):
        t = rechtliches.datenschutz(rig) + rechtliches.impressum(rig)
        for bad in ("petrick.top", "192.168.", "telegram@", ":8086", "Vault", "traefik", "fail2ban"):
            assert bad.lower() not in t.lower(), bad


def test_retention_matches_code():
    """Steht eine Frist im Text, muss sie zum Code passen – aendert sich der Code, faellt dieser Test auf."""
    import activity
    import events
    import resultcache
    t = rechtliches.datenschutz(True)
    assert activity.MAX_LOGINS == 20 and activity.LOGIN_DAYS == 30 and "die letzten 20, höchstens 30 Tage" in t
    assert events.KEEP_DAYS == 90 and "90 Tage" in t
    assert resultcache.CACHE_DAYS == 7 and "bis zu 7 Tage" in t
    assert "24 Stunden" in t and "1 km" in t


def test_contact_escaped_and_placeholders_detected(monkeypatch):
    k = {"name": "<b>X</b>", "anschrift": ["Weg 1", "12345 Ort"], "mail": "a@b.de", "stand": "2026-11-20"}
    monkeypatch.setattr(rechtliches, "kontakt", lambda: k)
    assert "<b>X</b>" not in rechtliches.impressum(False) and "&lt;b&gt;X&lt;/b&gt;" in rechtliches.impressum(False)
    assert rechtliches.offen(k) == []
    assert rechtliches.offen({**k, "anschrift": ["[STRASSE HAUSNUMMER]", "x"]}) == ["anschrift"]


def test_check_script_reports_placeholders():
    """Solange rechtliches.json Platzhalter hat, sperrt das Skript den Prod-Deploy (Exit 1); sonst 0."""
    r = subprocess.run([sys.executable, "werkzeuge/check_rechtliches.py"], capture_output=True, text=True)
    k = json.load(open("app/rechtliches.json", encoding="utf-8"))
    assert r.returncode == (1 if rechtliches.offen(k) else 0)


def test_mail_hidden_from_harvesters(monkeypatch):
    """Die Adresse steht nie im Klartext im HTML, der Browser zeigt sie aber normal an (Entities)."""
    import html
    k = {"name": "X", "anschrift": ["Weg 1", "12345 Ort"], "mail": "a@b.de", "stand": "2026-11-20"}
    monkeypatch.setattr(rechtliches, "kontakt", lambda: k)
    for t in (rechtliches.impressum(False), rechtliches.datenschutz(True)):
        assert "a@b.de" not in t and "mailto:" not in t
        assert 'href="mailto:a@b.de"' in html.unescape(t) and ">a@b.de</a>" in html.unescape(t)
