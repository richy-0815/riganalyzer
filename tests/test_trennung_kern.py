"""Trennung Ride/Rig, Phase 1 (seit 0.46.4): der Kern importiert keine Rig-Module.

Rig-Teile haengen sich ueber hooks.py ein (Anmeldung in rig_ext.py). Neue Module zaehlen automatisch zum Kern –
wer ein Rig-Modul anlegt, traegt es in RIG ein.
"""

import ast
import os

import pytest

APP = os.path.join(os.path.dirname(__file__), "..", "app")

RIG = {"rig", "sondermaut", "rig_ext", "rig_routes", "trips", "package", "corridor", "mapextract", "reisedaten", "shares", "fuelprice", "fuelprice_dk",
       "fuelprice_eu", "fuelprice_nordic", "frcamps", "navpois", "traffic", "reviews", "shops", "shops_build", "mapillary", "platzlaenge", "fotos"}
ENTRY = {"main"}   # Einstieg; selbst ohne Rig-Importe (Rig ueber hooks: app.router.rig, app.start …)


def _modules():
    return {f[:-3] for f in os.listdir(APP) if f.endswith(".py")}


def _imports(mod):
    tree = ast.parse(open(os.path.join(APP, mod + ".py"), encoding="utf-8").read())
    out = set()
    for n in ast.walk(tree):   # auch Importe innerhalb von Funktionen
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module.split(".")[0])
    return out


def test_rig_liste_ist_aktuell():
    assert RIG <= _modules(), f"RIG nennt Module, die es nicht gibt: {sorted(RIG - _modules())}"


@pytest.mark.parametrize("mod", sorted(_modules() - RIG))
def test_kern_importiert_kein_rig_modul(mod):
    bad = _imports(mod) & (RIG | ENTRY)
    assert not bad, f"Kern-Modul {mod} importiert {sorted(bad)} – ueber hooks.py anmelden statt importieren"


@pytest.mark.parametrize("mod", sorted(RIG))
def test_rig_importiert_main_nicht(mod):
    """Seit 0.46.6: Rig-Module haengen am Kern, nicht am Einstieg (main bedient bis Phase 2 beide Apps)."""
    assert "main" not in _imports(mod), f"Rig-Modul {mod} importiert main"


@pytest.fixture
def ohne_erweiterungen(monkeypatch):
    import hooks
    monkeypatch.setenv("RA_EXTENSIONS", "")
    monkeypatch.setattr(hooks, "_hooks", {})
    monkeypatch.setattr(hooks, "_loaded", None)
    return hooks


def test_hooks_laedt_rig_ext_als_voreinstellung(monkeypatch):
    import hooks
    import rig
    monkeypatch.delenv("RA_EXTENSIONS", raising=False)
    assert hooks.extensions() == ("rig_ext",)
    assert hooks.one("analyze.vehicle") is rig.collect


def test_hooks_fehlendes_modul_ist_ein_fehler(monkeypatch):
    import hooks
    monkeypatch.setenv("RA_EXTENSIONS", "gibt_es_nicht")
    monkeypatch.setattr(hooks, "_loaded", None)
    with pytest.raises(ImportError):
        hooks.all_("purge.user")


def test_hooks_doppelt_belegt(ohne_erweiterungen):
    hooks = ohne_erweiterungen
    hooks.add("x", len)
    hooks.add("x", len)   # dieselbe Funktion zaehlt einmal
    assert hooks.one("x") is len
    hooks.add("x", str)
    with pytest.raises(RuntimeError):
        hooks.one("x")


def test_kern_ohne_rig_meine_daten_und_loeschen(ohne_erweiterungen, data_dir):
    import mydata
    import purge
    out = mydata.export("olaf")
    assert "touren" not in out and "konto" in out and "verbrauch" in out
    labels = [x["label"] for x in mydata.overview("olaf", "rig")["gespeichert"]]
    assert "Touren" not in labels and "Konto" in labels
    assert "Touren" not in purge.purge_user("olaf")


def test_rig_teile_nur_in_der_rig_uebersicht(data_dir):
    import mydata
    ride = [x["label"] for x in mydata.overview("olaf", "ride")["gespeichert"]]
    rig = [x["label"] for x in mydata.overview("olaf", "rig")["gespeichert"]]
    assert "Touren" not in ride and "Touren" in rig and "Fahrzeugprofile" in rig
    werte = [x["label"] for x in mydata.overview("olaf", "rig")["monat"]["werte"]]
    assert "Navi-Routen" in werte
    assert "Navi-Routen" not in [x["label"] for x in mydata.overview("olaf", "ride")["monat"]["werte"]]
    assert {"touren", "platz_bewertungen", "geteilte_touren"} <= set(mydata.export("olaf"))


# ---------------------------------------------------------------- je App eine eigene FastAPI-App
def test_app_rig_parameter_schaltet_nicht_mehr_um():
    """Vorher machte "?app=rig" eine Ride-Anfrage zur Rig-Anfrage (Review 13.11.2026). Jetzt zaehlt nur der Host."""
    import main
    from fastapi.testclient import TestClient
    c = TestClient(main.app, base_url="https://rideanalyzer.petrick.top")
    assert "<title>RideAnalyzer" in c.get("/login?app=rig").text


def test_profil_geht_vor_host():
    """Phase 2: ein Container startet nur create_app(<app>) – die App weiss selbst, wer sie ist."""
    import main
    from fastapi.testclient import TestClient
    rig = TestClient(main.create_app("rig"), base_url="https://rideanalyzer.petrick.top")
    assert "<title>RigAnalyzer" in rig.get("/login").text
    ride = TestClient(main.create_app("ride"), base_url="https://riganalyzer.petrick.top")
    assert "<title>RideAnalyzer" in ride.get("/login").text


def test_rig_router_nur_in_der_rig_app():
    import main
    import rig_routes
    ride, rig = main.create_app("ride"), main.create_app("rig")

    def has(app):
        return any(getattr(r, "original_router", None) is rig_routes.rig or getattr(r, "path", None) == "/api/trips"
                   for r in app.router.routes)
    assert has(rig) and not has(ride)


def test_host_verteiler():
    import web
    assert web.app_of_host("riganalyzer-qa.example.org") == "rig"
    assert web.app_of_host("riganalyzer.localhost:8766") == "rig"
    assert web.app_of_host("rideanalyzer.petrick.top") == "ride"
    assert web.app_of_host("evil.example") == "ride" and web.app_of_host("") == "ride"
