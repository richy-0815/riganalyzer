"""Stellplatz-Laenge (0.53.0, #30): Suchmuster, OSM-Masse, Urteil, Luftbild-Mosaik, Warteschlange."""

import io

import pytest

import platzlaenge as pl


@pytest.fixture(autouse=True)
def frisch(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "DATA", str(tmp_path))
    monkeypatch.setattr(pl, "FILE", str(tmp_path / "pl.sqlite"))
    monkeypatch.setattr(pl, "IMG_DIR", str(tmp_path / "img"))
    monkeypatch.setattr(pl._tls, "con", None, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield


# ---------------------------------------------------------------- Suchmuster
@pytest.mark.parametrize("text,kind,m", [
    ("Stellplätze für Wohnmobile bis 8 m Länge.", "max", 8.0),
    ("Max. Länge: 10,5 m – Ver- und Entsorgung vorhanden.", "max", 10.5),
    ("Fahrzeuge über 7 m sind nicht erlaubt.", "max", 7.0),
    ("Aire pour camping-cars jusqu'à 12 mètres.", "max", 12.0),
    ("Camping-cars de plus de 8 m interdits.", "max", 8.0),
    ("Pitches for motorhomes up to 9m length.", "max", 9.0),
    ("Vehicles longer than 10 m are not allowed.", "max", 10.0),
    ("Area sosta camper, lunghezza massima 11 m.", "max", 11.0),
    ("Parcelas para autocaravanas hasta 10 m.", "max", 10.0),
    ("Camperplaatsen, maximale lengte 9 meter.", "max", 9.0),
    ("Auch für Wohnmobile über 12 m geeignet.", "min", 12.0),
    ("Emplacements pour camping-cars de plus de 10 m bienvenus.", "min", 10.0),
])
def test_text_erkennt_laenge(text, kind, m):
    hit = pl.from_text(text)
    assert hit and hit["kind"] == kind and hit["m"] == m
    assert hit["quote"]


@pytest.mark.parametrize("text", [
    "Der Stellplatz liegt 500 m vom Strand entfernt.",
    "Wohnmobilstellplatz bis 200 m zum Zentrum.",
    "Aire de camping-car à 800 m du centre.",
    "Schöner Platz mit 12 Stellplätzen.",
    "Höhe max. 3,2 m (Schranke)",            # keine Laenge (unter 5 m)
    "",
])
def test_text_ohne_laenge(text):
    hit = pl.from_text(text)
    assert not hit or not hit.get("m")


def test_text_strengere_angabe_gewinnt():
    hit = pl.from_text("Plätze für Wohnmobile bis 12 m. Hinten bis 8 m Länge.")
    assert hit["m"] == 8.0


def test_text_gespann_verbot():
    hit = pl.from_text("Wohnmobilstellplatz – keine Gespanne, bitte.")
    assert hit["trailer"] is False and not hit.get("m")


def test_html_text():
    t = pl.html_text("<html><script>var x=1</script><p>Bis&nbsp;10 m</p><br>Danke</html>")
    assert "var x" not in t and "Bis\xa010 m" in t and "Danke" in t


def test_subpages_nur_eigene_seite():
    html = '<a href="/stellplatz">Stellplatz</a><a href="https://fremd.de/wohnmobil">x</a><a href="/impressum">Impressum</a>'
    assert pl._subpages("https://platz.de/", html) == ["https://platz.de/stellplatz"]


def test_keine_internen_adressen(monkeypatch):
    monkeypatch.setattr(pl.socket, "getaddrinfo", lambda h, p: [(0, 0, 0, "", ("10.0.0.5", 0))])
    assert not pl._public_host("intern.example")
    monkeypatch.setattr(pl.socket, "getaddrinfo", lambda h, p: [(0, 0, 0, "", ("93.184.216.34", 0))])
    assert pl._public_host("example.com")


# ---------------------------------------------------------------- Geometrie / Zufahrt / Urteil
def _rect(lat, lon, lang_m, breit_m):
    dl, dw = lang_m / 111320, breit_m / 111320 / 0.7
    return [(lat, lon), (lat, lon + dw), (lat + dl, lon + dw), (lat + dl, lon), (lat, lon)]


def test_rect_dims():
    a, b = pl.rect_dims(_rect(45.57, 6.0, 12, 4))
    assert a == pytest.approx(12, abs=0.5) and b == pytest.approx(4, abs=0.5)


def test_geometry_facts_stellflaechen():
    site = {"outline": _rect(45.57, 6.0, 80, 40), "pitches": [_rect(45.57, 6.0, 10, 4), _rect(45.57, 6.001, 8, 4), _rect(45.57, 6.002, 9, 4)]}
    g = pl.geometry_facts(site)
    assert g["stellflaechen"] == 3 and g["stellflaeche_max_m"] == pytest.approx(10, abs=0.5)
    assert g["platz_m"][0] == pytest.approx(80, abs=2)


def test_access_hints():
    road = {"tags": {"highway": "unclassified", "maxlength": "10"}, "geom": [(45.5701, 6.0), (45.5702, 6.0)]}
    narrow = {"tags": {"highway": "residential", "narrow": "yes", "trailer": "no"}, "geom": [(45.572, 6.0)]}
    h = pl.access_hints({"roads": [narrow, road]}, 45.57, 6.0)
    assert h[0] == {"was": "Längenbeschränkung", "wert": 10.0, "abstand": 11}
    assert {x["was"] for x in h} >= {"Engstelle", "Anhänger verboten"}


def test_verdict_text_vor_luftbild():
    v = pl.verdict({"text": {"kind": "max", "m": 9.0, "src": "web", "quote": "bis 9 m"},
                    "luft": {"bild_brauchbar": True, "sicherheit": "hoch", "max_fahrzeuglaenge_m": 14}})
    assert v == {"m": 9.0, "kind": "max", "level": "confirmed", "src": "Website", "why": "bis 9 m"}


def test_verdict_luftbild_mit_zufahrt_deckel():
    v = pl.verdict({"luft": {"stellbahn_m": 40, "rangier_m": 15, "offen": 0.6, "flaeche_m2": 2000},
                    "zufahrt": [{"was": "Längenbeschränkung", "wert": 10.0, "abstand": 80}]})
    assert v["m"] == 10.0 and v["level"] == "likely" and "Längenbeschränkung" in v["why"]


def test_verdict_luftbild_wenden_begrenzt():
    v = pl.verdict({"luft": {"stellbahn_m": 46, "rangier_m": 9.5, "offen": 0.4, "flaeche_m2": 9000}})
    assert v == {"m": 10.5, "kind": "max", "level": "likely", "src": "Luftbild-Messung",
                 "why": "freie Bahn bis 46 m, Rangierfläche 9,5 × 9,5 m"}


def test_verdict_grosser_freier_platz_mindestens_18():
    v = pl.verdict({"luft": {"stellbahn_m": 53, "rangier_m": 23, "offen": 0.62, "flaeche_m2": 3100}})
    assert v["m"] == 18.0 and v["kind"] == "min"


def test_verdict_stellflaechen_vor_freier_bahn():
    v = pl.verdict({"luft": {"stellbahn_m": 50, "rangier_m": 20, "offen": 0.7, "flaeche_m2": 3000},
                    "geo": {"stellflaechen": 6, "stellflaeche_max_m": 9.0}})
    assert v["m"] == 9.0 and "Stellflächen in OSM" in v["why"]


def test_verdict_kleiner_oder_zugewachsener_platz_ohne_urteil():
    assert pl.verdict({"luft": {"stellbahn_m": 20, "rangier_m": 8, "offen": 0.1, "flaeche_m2": 2000}}) is None
    assert pl.verdict({"luft": {"stellbahn_m": 20, "rangier_m": 8, "offen": 0.5, "flaeche_m2": 100}}) is None
    v = pl.verdict({"geo": {"stellflaechen": 4, "stellflaeche_max_m": 11.2}})
    assert v["src"] == "Stellflächen in OSM" and v["m"] == 11.2


# ---------------------------------------------------------------- Messung im Bild
def _bild(w, h, frei):
    """Waldbild (dunkelgruen) mit freien Schotterflaechen frei=[(x0, y0, x1, y1)]."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), (40, 80, 35))
    d = ImageDraw.Draw(img)
    for box in frei:
        d.rectangle(box, fill=(205, 195, 175))
    return img


def test_open_mask_wald_und_schatten_zu():
    import numpy as np
    from PIL import Image
    a = np.array([[[40, 80, 35], [205, 195, 175], [50, 50, 50], [150, 190, 90]]], np.uint8)
    m = pl.open_mask(Image.fromarray(a))
    assert m.tolist() == [[False, True, False, True]]   # Wald, Schotter, Schatten, helle Wiese


def test_longest_strip_waagerecht_und_schraeg():
    import numpy as np
    m = np.zeros((200, 300), bool)
    m[90:110, 20:270] = True                  # 250 x 20 px Bahn
    n, ecken = pl.longest_strip(m, 15)
    assert 240 <= n <= 250 and len(ecken) == 4
    assert all(m[int(round(y)) - (1 if y >= 110 else 0), min(int(round(x)), 269)] for x, y in ecken)
    from PIL import Image
    r = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).rotate(40, expand=True)) > 0
    n2, _ = pl.longest_strip(r, 15)
    assert n2 >= 220                           # schraeg gedreht wird sie trotzdem gefunden


def test_largest_square():
    import numpy as np
    m = np.zeros((100, 100), bool)
    m[10:40, 10:70] = True
    assert pl.largest_square(m) == 30


def test_measure_misst_in_metern():
    # 0,25 m/px: freie Bahn 60 x 4 m und ein Wendeplatz 16 x 16 m, Umriss = ganzes Bild
    img = _bild(400, 200, [(40, 20, 280, 35), (280, 20, 343, 83)])
    shot = {"img": img, "mpp": 0.25, "px": lambda la, lo: (lo, la)}
    outline = [(0, 0), (0, 399), (199, 399), (199, 0)]
    m, jpeg = pl.measure(shot, outline)
    assert 72 <= m["stellbahn_m"] <= 77      # 240 + 63 px * 0,25 m (Bahn laeuft durch den Wendeplatz)
    assert 15 <= m["rangier_m"] <= 16.5
    assert m["flaeche_m2"] == pytest.approx(400 * 200 * 0.0625, rel=0.02)
    assert jpeg[:2] == b"\xff\xd8"


# ---------------------------------------------------------------- Luftbild-Mosaik
class _TileClient:
    def __init__(self, missing=()):
        self.urls, self.missing = [], missing

    def get(self, url, **kw):
        from PIL import Image
        self.urls.append(url)

        class R:
            pass
        r = R()
        if any(m in url for m in self.missing):
            r.status_code, r.headers, r.content = 404, {"content-type": "text/xml"}, b""
            return r
        img = Image.effect_noise((256, 256), 60).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        r.status_code, r.headers, r.content = 200, {"content-type": "image/jpeg"}, buf.getvalue()
        return r


def test_aerial_nimmt_nationale_quelle_und_faellt_zurueck():
    lat, lon = 43.80, 6.32
    box = (lat - 0.0005, lat + 0.0005, lon - 0.0007, lon + 0.0007)
    shot = pl.aerial(lat, lon, box, client=_TileClient())
    assert shot["src"]["id"] == "ign" and shot["z"] == 19 and 0.1 < shot["mpp"] < 0.3
    assert 100 < shot["img"].width <= pl.MAX_PX and 100 < shot["img"].height <= pl.MAX_PX
    x, y = shot["px"](lat, lon)                 # Platzmitte liegt in der Bildmitte
    assert abs(x - shot["img"].width / 2) < 3 and abs(y - shot["img"].height / 2) < 3
    assert pl.aerial(lat, lon, box, client=_TileClient(missing=("geopf.fr",)))["src"]["id"] == "esri"


def test_aerial_grosser_platz_kleinerer_zoom():
    lat, lon = 52.0, 5.0
    box = (lat - 0.004, lat + 0.004, lon - 0.006, lon + 0.006)   # ~900 x 800 m
    shot = pl.aerial(lat, lon, box, client=_TileClient())
    assert shot["src"]["id"] == "pdok" and shot["z"] < 20


# ---------------------------------------------------------------- Ablauf
def test_analyze_text_reicht_ohne_luftbild(monkeypatch):
    monkeypatch.setattr(pl, "osm_site", lambda *a: {"outline": None, "pitches": [], "roads": []})
    monkeypatch.setattr(pl, "website_text", lambda url: pytest.fail("Website nicht noetig"))
    res = pl.analyze({"key": "node/1", "lat": 45.0, "lon": 6.0, "tags": {"description": "Wohnmobile bis 10 m", "website": "https://x.de"}})
    assert res["verdict"]["m"] == 10.0 and res["verdict"]["src"] == "Beschreibung in OSM"
    assert pl.cached_verdict("node/1")["m"] == 10.0


def test_analyze_website_dann_luftbild(monkeypatch):
    outline = _rect(45.0, 6.0, 60, 40)
    monkeypatch.setattr(pl, "osm_site", lambda *a: {"outline": outline, "pitches": [], "roads": []})
    monkeypatch.setattr(pl, "website_text", lambda url: [(url, "Herzlich willkommen auf unserem Platz.")])
    monkeypatch.setattr(pl, "aerial", lambda *a, **k: {"img": None, "src": pl.SOURCES[0], "z": 19, "mpp": 0.2, "px": None})
    monkeypatch.setattr(pl, "measure", lambda shot, pts, pitches: ({"stellbahn_m": 30.0, "rangier_m": 12.0, "offen": 0.5, "flaeche_m2": 2400}, b"jpg"))
    res = pl.analyze({"key": "way/7", "lat": 45.0, "lon": 6.0, "tags": {"tourism": "caravan_site", "website": "https://x.de"}})
    assert res["verdict"]["m"] == 13.0 and res["verdict"]["src"] == "Luftbild-Messung"
    assert pl.image_path("way/7").endswith("way_7.jpg")
    pub = pl.public("way/7", res)
    assert pub["luft"]["rangier_m"] == 12.0 and pub["luft"]["img"] is True


def test_analyze_ohne_umriss_kein_luftbild(monkeypatch):
    monkeypatch.setattr(pl, "osm_site", lambda *a: {"outline": None, "pitches": [], "roads": []})
    monkeypatch.setattr(pl, "aerial", lambda *a, **k: pytest.fail("ohne Umriss keine Messung"))
    res = pl.analyze({"key": "node/8", "lat": 45.0, "lon": 6.0, "tags": {}})
    assert res["verdict"] is None and "luft" not in res


def test_lookup_reiht_ein_und_liefert_gespeichertes():
    out = pl.lookup([{"key": "node/5", "lat": 1, "lon": 2}, {"key": "way/6", "lat": 1, "lon": 2}])
    assert out == {"node/5": {"state": "pending"}, "way/6": {"state": "pending"}}
    assert pl.status() == {"luftbild": True, "queue": 2}
    assert pl._next()[0] == "node/5"
    pl._put("node/5", {"verdict": {"m": 9.0, "kind": "max", "level": "confirmed", "src": "Website", "why": ""}, "at": "2026-11-22"})
    out = pl.lookup([{"key": "node/5", "lat": 1, "lon": 2}])
    assert out["node/5"]["state"] == "done" and out["node/5"]["verdict"]["m"] == 9.0
    assert pl.status()["queue"] == 1


def test_fehler_wird_spaeter_wiederholt(monkeypatch):
    pl._put("node/9", {"error": "X", "verdict": None}, ok=False)
    assert pl._get("node/9") is not None
    real = pl.time.time
    monkeypatch.setattr(pl.time, "time", lambda: real() + pl.RETRY_ERR + 5)
    assert pl._get("node/9") is None


def test_valid_key():
    assert pl.valid_key("way/123") and pl.valid_key("dt:abc-123")
    assert not pl.valid_key("../etc") and not pl.valid_key("way/12; drop")


# ---------------------------------------------------------------- Nachtplaetze
def test_nights_fits_mit_laenge():
    import nights
    veh = {"length": 12, "weight": 7}
    assert nights._fits({}, {}, veh, {"m": 10, "kind": "max", "level": "confirmed"}) == "no"
    assert nights._fits({}, {}, veh, {"m": 10, "kind": "max", "level": "likely"}) == "likely_no"
    assert nights._fits({}, {}, veh, {"m": 13, "kind": "max", "level": "likely"}) == "likely"
    assert nights._fits({"maxweight": 7.5}, {}, veh, {"m": 13, "kind": "max", "level": "confirmed"}) == "ok"
    assert nights._fits({}, {}, veh) == "unknown"


def test_maxlength_none_heisst_ohne_begrenzung(monkeypatch):
    monkeypatch.setattr(pl, "osm_site", lambda *a: {"outline": None, "pitches": [], "roads": []})
    res = pl.analyze({"key": "way/2", "lat": 43.8, "lon": 6.2, "tags": {"maxlength": "none"}})
    assert res["verdict"]["kind"] == "none" and res["verdict"]["level"] == "confirmed" and res["verdict"]["m"] > 20


# ---------------------------------------------------------------- eigener Auswerte-Prozess
def test_resolve_dt_aus_eigener_liste(monkeypatch):
    import frcamps
    it = {"id": "abc", "n": "Aire", "d": "Camping-cars jusqu'à 8 m.", "la": 43.8, "lo": 6.2}
    monkeypatch.setattr(frcamps, "_near", lambda la, lo, r: [(5.0, it)])
    assert pl.resolve({"key": "dt:abc", "lat": 43.8, "lon": 6.2})["fr"] is it
    assert pl.resolve({"key": "dt:xyz", "lat": 43.8, "lon": 6.2}) is None
    osm = {"key": "way/3", "lat": 43.8, "lon": 6.2}
    assert pl.resolve(osm) is osm                       # OSM-Plaetze: Merkmale holt analyze() per Overpass


def test_analyze_holt_tags_per_overpass(monkeypatch):
    monkeypatch.setattr(pl, "osm_site", lambda *a: {"outline": None, "pitches": [], "roads": [], "ok": True,
                                                    "tags": {"description": "Wohnmobile bis 9 m"}})
    assert pl.analyze({"key": "node/5", "lat": 52.0, "lon": 13.0})["verdict"]["m"] == 9.0


def test_overpass_weg_wird_spaeter_wiederholt(monkeypatch):
    monkeypatch.setattr(pl, "osm_site", lambda *a: {"outline": None, "pitches": [], "roads": [], "ok": False, "tags": None})
    monkeypatch.setattr(pl.time, "sleep", lambda s: None)
    pl.lookup([{"key": "node/6", "lat": 52.0, "lon": 13.0}])
    pl.run_worker(once=True)
    assert pl._get("node/6")["error"] == "RuntimeError" and pl.cached_verdict("node/6") is None
    assert pl.status()["queue"] == 0


def test_run_worker_arbeitet_schlange_ab(monkeypatch):
    import frcamps
    monkeypatch.setattr(frcamps, "_near", lambda la, lo, r: [])
    monkeypatch.setattr(pl, "osm_site", lambda *a: {"outline": None, "pitches": [], "roads": [], "ok": True,
                                                    "tags": {"description": "Wohnmobile bis 9 m"}})
    pl.lookup([{"key": "node/5", "lat": 52.0, "lon": 13.0}, {"key": "dt:weg", "lat": 52.0, "lon": 13.0}])
    pl.run_worker(once=True)
    assert pl.status()["queue"] == 0
    assert pl.cached_verdict("node/5")["m"] == 9.0
    assert pl._get("dt:weg")["unbekannt"] is True


def test_api_reiht_nur_ein(monkeypatch):
    """Die Web-Anfrage fasst weder OSM-DB noch Websites an – nur die Warteschlange."""
    import rig_routes
    src = open(rig_routes.__file__, encoding="utf-8").read()
    body = src[src.index("async def api_camps_laenge("):src.index("async def api_camps_laenge_bild(")]
    assert "osmdb" not in body and "frcamps" not in body and "website" not in body
