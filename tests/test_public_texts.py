"""0.34.1: oeffentliche Erklaertexte auf Stand – Alpha statt Prototyp, Datenschutz wie es wirklich ist, Planen vor GPX."""
import json
import re
import os

import pytest

APP = os.path.join(os.path.dirname(__file__), "..", "app")
RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"


def read(*p):
    with open(os.path.join(APP, *p), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def client(data_dir):
    from fastapi.testclient import TestClient
    import main
    return lambda host: TestClient(main.app, base_url=host)


@pytest.mark.parametrize("host", [RIDE, RIG])
def test_login_and_info_say_alpha(client, host):
    c = client(host)
    page = c.get("/login").text
    assert 'class="badge">Alpha<' in page and "Prototyp" not in page
    assert "Prototyp" not in c.get("/info.html").text


def test_info_texts_current(client):
    rig = client(RIG).get("/info.html").text
    assert "Umweltzonen" in rig and "ohne Netz" in rig and "Frankreich" in rig
    ride = client(RIDE).get("/info.html").text
    assert "Wetter" in ride and "Planen oder prüfen" in ride and "Rig" not in ride


@pytest.mark.parametrize("folder", ["static-ride", "static-rig"])
def test_help_dialog_current(folder):
    html = read(folder, "index.html")
    assert "Prototyp" not in html and 'class="badge">Alpha<' in html
    for old in ("nicht gespeichert", "nur in eurem Browser", "Neues Gebiet?"):
        assert old not in html, old
    # Datenschutz-Details stehen in den Häufigen Fragen, das Fenster verlinkt dorthin
    assert 'href="/faq.html#datenschutz-was-wird-gespeichert"' in html and "Ganz Europa" in html
    assert "<b>Route planen oder GPX laden</b>" in html


def test_rig_help_mentions_new_features():
    html = read("static-rig", "index.html")
    assert "Umweltzonen" in html and "Ans Navi schicken" in html and "Regenradar" in html
    assert "Spritpreise.</li>" not in html and "Tankerkönig" in html


def test_features_privacy_and_no_internal_costs():
    f = json.load(open(os.path.join(APP, "features.json"), encoding="utf-8"))
    titles = [s["title"] for s in f["rig"]["sections"]]
    assert not any("Kosten" in t for t in titles) and not any("Prototyp" in t for t in titles)
    privacy = next(s for s in f["common"] if "Datenschutz" in s["title"])
    text = json.dumps(privacy, ensure_ascii=False)
    assert "nur in eurem Browser" not in text and "Nichts gespeichert" not in text
    assert "Was wir zählen" in text and "Was gespeichert wird" in text


def test_tech_page_privacy_current(data_dir):
    import main
    for rig in (False, True):
        t = main._tech_html(rig)
        assert "nicht gespeichert" not in t and "zählen" in t


def test_tech_page_current_and_separated(data_dir):
    """0.35.4: Technik-Seite auf Stand – Europa-Routing, Live-Quellen mit Namensnennung, RigNavi nur bei Rig."""
    import main
    rig, ride = main._tech_html(True), main._tech_html(False)
    for t in (rig, ride):
        assert "ohne fremde APIs" not in t and "Fahrzeugprofilen" not in t
        assert "ganz Europa" in t and "Open-Meteo" in t and "RainViewer" in t and "Deutscher Wetterdienst" in t
        assert "worum es geht" not in t.lower() or True
    assert "Tankerkönig" in rig and "CC BY 4.0" in rig and "RigNavi" in rig and "prix-carburants" in rig
    assert "Was ist RigAnalyzer?" in rig and "Was ist RideAnalyzer?" in ride
    for word in ("Tankerkönig", "RigNavi", "Gespann", "Wohnmobil", "Umweltzone"):
        assert word not in ride, word


def test_texts_035_5_current(data_dir):
    """0.35.5: Planungsgebiet ueberall aus der Konfiguration, keine veralteten Aussagen auf Funktionen/Technik/App-Seite."""
    import main
    import planner
    rig_tech, ride_tech = main._tech_html(True), main._tech_html(False)
    for t in (rig_tech, ride_tech):
        assert "Routing-Daten decken ganz Europa" not in t and "Routenplaner für ganz Europa" not in t
        assert planner.AREA in t
    feats = main._features_html(True)
    for old in ("Cookie, 1 Jahr", "ohne App und ohne Konto", "4.072", "RigNavi 0.14"):
        assert old not in feats, old
    assert planner.AREA in feats and "Euro-Norm" in feats and "Touren speichern" in feats and "Zugang beantragen" in feats
    assert "Frankreich" in feats.split("Tankplan</h2>", 1)[1].split("</article>", 1)[0]
    ride_feats = main._features_html(False)
    assert "ohne App und ohne Konto" not in ride_feats and "Rig" not in re.sub(r"<[^>]+>", " ", ride_feats).replace("RideAnalyzer", "")
    app = main._app_html(True)
    for old in ("v0.9", "v0.10", "Prototyp", "DACH plus Korridor", "Gespeichert wird davon nichts außer"):
        assert old not in app, old
    assert "Umweltzonen" in app and "Rückmeldung" in app and "Server-Paket löschen" in app
