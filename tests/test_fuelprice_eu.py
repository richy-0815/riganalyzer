"""Echte Dieselpreise FR/ES/IT (seit v0.23.0): Behoerdendaten parsen, OSM-Tankstelle <= 150 m zuordnen, Pausen."""
import pytest

import fuelprice
import fuelprice_eu as eu
from test_admin import basic, env  # noqa: F401  (Fixture env)

from datetime import datetime, timedelta, timezone

RECENT = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
OLD = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
FR_ROWS = [{"id": 1, "geom": {"lon": 6.5101, "lat": 43.8501}, "gazole_prix": 1.689, "gazole_maj": RECENT,
            "adresse": "Route de Moustiers", "ville": "Castellane"},
           {"id": 2, "geom": {"lon": 6.53, "lat": 43.86}, "gazole_prix": None, "gazole_maj": None}]
ES_DATA = {"Fecha": "31/10/2026 20:30:12", "ListaEESSPrecio": [
    {"Latitud": "36,721300", "Longitud (WGS84)": "-4,421600", "Precio Gasoleo A": "1,459", "Rótulo": "REPSOL ", "Localidad": "MALAGA"},
    {"Latitud": "36,800000", "Longitud (WGS84)": "-4,500000", "Precio Gasoleo A": "", "Rótulo": "SIN DIESEL"}]}
IT_PRICES_SEMI = """Estrazione del 2026-10-31
idImpianto;descCarburante;prezzo;isSelf;dtComu
100;Gasolio;1.799;0;31/10/2026 07:10:00
100;Gasolio;1.659;1;31/10/2026 07:10:00
100;Benzina;1.849;1;31/10/2026 07:10:00
200;Blue Diesel;1.899;1;30/10/2026 18:00:00
"""
IT_PLANTS_PIPE = """Estrazione del 2026-10-31
idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine
100|ROSSI|Q8|Stradale|Q8 Rossi|Via Roma 1|AOSTA|AO|45.7371|7.3201
200|BIANCHI|IP|Stradale|IP|Via Po 2|TORINO|TO|45.07|7.68
"""


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    eu._cache.clear()
    eu._grids.clear()
    eu._paused.clear()
    eu._loading.clear()
    monkeypatch.setattr(eu, "BACKGROUND", False)
    calls = {"FR": 0, "ES": 0, "IT": 0}

    def fr(lat, lon):
        calls["FR"] += 1
        return FR_ROWS

    def es():
        calls["ES"] += 1
        return ES_DATA

    def it():
        calls["IT"] += 1
        return IT_PRICES_SEMI, IT_PLANTS_PIPE
    monkeypatch.setattr(eu, "_fr_fetch", fr)
    monkeypatch.setattr(eu, "_es_fetch", es)
    monkeypatch.setattr(eu, "_it_fetch", it)
    return calls


def test_france_match_and_cache(clean):
    st = [("node/1", 43.8500, 6.5100), ("node/2", 43.8600, 6.5300), ("node/3", 43.8550, 6.5200)]
    res = eu.prices(st)
    assert res["node/1"]["diesel"] == 1.689 and res["node/1"]["src"] == "prix-carburants" and res["node/1"]["cc"] == "FR"
    assert "Castellane" in res["node/1"]["name"] and res["node/1"]["open"] is None
    assert "node/2" not in res and "node/3" not in res          # ohne Dieselpreis bzw. keine Station <= 150 m
    eu.prices(st)
    assert clean["FR"] == 1


def test_spain_comma_decimals(clean):
    res = eu.prices([("way/5", 36.7214, -4.4217)])
    assert res["way/5"]["diesel"] == 1.459 and res["way/5"]["cc"] == "ES" and res["way/5"]["name"] == "REPSOL MALAGA"
    eu.prices([("way/5", 36.7214, -4.4217)])
    assert clean["ES"] == 1                                       # Gesamtliste 30 min gemerkt


def test_italy_self_service_and_both_separators(clean):
    res = eu.prices([("n/7", 45.7372, 7.3202), ("n/8", 45.07, 7.68)])
    assert res["n/7"]["diesel"] == 1.659 and res["n/7"]["src"] == "MIMIT 8 Uhr"   # Self statt Bedienung
    assert "n/8" not in res                                       # nur „Blue Diesel“, kein Gasolio


def test_germany_not_asked(clean):
    assert eu.prices([("x", 52.5, 13.4)]) == {} and clean == {"FR": 0, "ES": 0, "IT": 0}


def test_pause_only_failing_source(clean, monkeypatch):
    def boom(lat, lon):
        clean["FR"] += 1
        raise eu.httpx.ConnectError("weg")
    monkeypatch.setattr(eu, "_fr_fetch", boom)
    st = [("f", 43.85, 6.51), ("s", 36.7214, -4.4217)]
    res = eu.prices(st)
    assert "s" in res and "f" not in res and eu._paused["FR"] > eu.time.time() + 800
    eu.prices(st)
    assert clean["FR"] == 1                                       # Frankreich ruht, Spanien laeuft weiter


def test_api_mixes_sources(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    monkeypatch.setattr(fuelprice, "KEY", "")                     # DE aus
    r = env.post("/api/fuel/prices", json={"stations": [["node/1", 43.85, 6.51], ["way/5", 36.7214, -4.4217]]},
                 headers=basic("olaf"))
    j = r.json()
    assert r.status_code == 200 and set(j["prices"]) == {"node/1", "way/5"}
    assert set(j["sources"]) == {"FR", "ES"} and "prix-carburants" in j["source"]


def test_fresh_refetches_only_after_two_minutes(clean, monkeypatch):
    st = [("node/1", 43.8500, 6.5100)]
    eu.prices(st)
    eu.prices(st, fresh=True)
    assert clean["FR"] == 1                                       # gerade erst geholt: kein neuer Abruf
    t0 = eu.time.time()
    monkeypatch.setattr(eu.time, "time", lambda: t0 + 300)
    eu.prices(st)
    assert clean["FR"] == 1                                       # normal: 10 min gemerkt
    eu.prices(st, fresh=True)
    assert clean["FR"] == 2                                       # „Jetzt prüfen“: aelter als 2 min -> neu


def test_api_fresh_and_checked(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    monkeypatch.setattr(fuelprice, "KEY", "")
    seen = []
    monkeypatch.setattr(eu, "lookup", lambda st, fresh=False: (seen.append(fresh), ({}, {"ES"}))[1])
    j = env.post("/api/fuel/prices", json={"stations": [["n", 43.85, 6.51]], "fresh": True}, headers=basic("olaf")).json()
    assert seen == [True] and j["checked"] > 0 and j["pending"] == ["ES"]


def test_france_wider_radius_but_unambiguous(clean, monkeypatch):
    rows = [{"geom": {"lon": 4.8470, "lat": 44.9580}, "gazole_prix": 2.25, "gazole_maj": RECENT, "ville": "Cornas"},   # 3 Stellen
            {"geom": {"lon": 4.9000, "lat": 44.9000}, "gazole_prix": 2.10, "gazole_maj": RECENT, "ville": "A"},
            {"geom": {"lon": 4.9030, "lat": 44.9000}, "gazole_prix": 2.30, "gazole_maj": RECENT, "ville": "B"},
            {"geom": {"lon": 4.8000, "lat": 44.8000}, "gazole_prix": 1.90, "gazole_maj": OLD, "ville": "Alt"}]
    monkeypatch.setattr(eu, "_fr_fetch", lambda lat, lon: rows)
    res = eu.prices([("cornas", 44.9603, 4.8488),      # ~290 m daneben, sonst nichts in der Naehe -> passt
                     ("mitte", 44.9000, 4.9015),       # A und B je ~120 m -> naechste innerhalb 150 m zaehlt
                     ("zwischen", 44.9030, 4.9015),    # A/B ~360 m, fast gleich weit -> lieber schaetzen
                     ("alt", 44.8000, 4.8000)])        # Preis 9 Tage alt -> zaehlt nicht
    assert res["cornas"]["diesel"] == 2.25 and "mitte" in res
    assert "zwischen" not in res and "alt" not in res


def test_background_list_does_not_block(clean, monkeypatch):
    import threading
    monkeypatch.setattr(eu, "BACKGROUND", True)
    gate = threading.Event()

    def slow():
        gate.wait(5)
        clean["ES"] += 1
        return ES_DATA
    monkeypatch.setattr(eu, "_es_fetch", slow)
    out, pending = eu.lookup([("way/5", 36.7214, -4.4217)])
    assert out == {} and pending == {"ES"}                        # Anfrage wartet nicht auf die Liste
    gate.set()
    for _ in range(50):
        if "ES" in eu._grids:
            break
        eu.time.sleep(0.05)
    out, pending = eu.lookup([("way/5", 36.7214, -4.4217)])
    assert out["way/5"]["diesel"] == 1.459 and pending == set() and clean["ES"] == 1


def test_timeout_pauses_briefly_it_long(clean, monkeypatch):
    def slow(lat, lon):
        raise eu.httpx.ReadTimeout("zaeh")

    def gone():
        raise eu.httpx.ConnectTimeout("weg")
    monkeypatch.setattr(eu, "_fr_fetch", slow)
    monkeypatch.setattr(eu, "_it_fetch", gone)
    eu.prices([("f", 43.85, 6.51), ("i", 45.737, 7.32)])
    now = eu.time.time()
    assert now + 60 < eu._paused["FR"] < now + 200 and eu._paused["IT"] > now + 3000
