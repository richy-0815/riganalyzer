"""Unwetterwarnungen entlang der Route (seit v0.13): DWD-Polygone (DE), MeteoAlarm mit Gebietsgrenzen (FR NUTS3, seit 0.40.0
auch EMMA_ID/NUTS2/NUTS3 und Feed-Polygone), sonst orange/rot je Land."""
import datetime as dt

import pytest

import weather

UTC = dt.timezone.utc
T0 = dt.datetime(2027, 5, 24, 8, 0, tzinfo=UTC)


def _square(lon0, lat0, d=0.2):
    return {"type": "Polygon", "coordinates": [[[lon0, lat0], [lon0 + d, lat0], [lon0 + d, lat0 + d], [lon0, lat0 + d], [lon0, lat0]]]}


def _dwd(features):
    return {"type": "FeatureCollection", "features": features}


def _dwd_feat(lon0, lat0, sev="Moderate", ev="GEWITTER", onset="2027-05-24T10:00:00Z", expires="2027-05-24T20:00:00Z"):
    return {"type": "Feature", "geometry": _square(lon0, lat0),
            "properties": {"SEVERITY": sev, "EVENT": ev, "ONSET": onset, "EXPIRES": expires, "AREADESC": "Gießen"}}


def _ma(level, nuts=None, emma=None, area="Var", onset="2027-05-24T10:00:00+02:00", expires="2027-05-24T22:00:00+02:00",
        typ="3; Thunderstorm"):
    code = {"value": nuts, "valueName": "NUTS3"} if nuts else {"value": emma or "X", "valueName": "EMMA_ID"}
    info = {"language": "en-GB", "event": "Thunderstorm warning", "onset": onset, "expires": expires,
            "area": [{"areaDesc": area, "geocode": [code]}],
            "parameter": [{"valueName": "awareness_level", "value": f"2; {level}; Moderate"},
                          {"valueName": "awareness_type", "value": typ}]}
    return {"alert": {"info": [info]}}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    weather._feed_cache.clear()
    calls = []

    def fake_get_json(url, params=None):
        calls.append(url)
        return FEEDS[url.rsplit("/", 1)[-1] if "meteoalarm" in url else "dwd"]

    monkeypatch.setattr(weather, "_get_json", fake_get_json)
    return calls


FEEDS = {}


def _stage(pts, n=1):
    return [(n, [(lat, lon, T0 + dt.timedelta(hours=h), km) for lat, lon, h, km in pts])]


def test_dwd_polygon_hits_route(monkeypatch):
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "germany")
    FEEDS["dwd"] = _dwd([_dwd_feat(8.5, 50.5), _dwd_feat(12.0, 54.0)])     # zweite weit weg
    w = weather.warnings_for(_stage([(50.4, 8.4, 0, 0), (50.8, 8.8, 4, 80)]))[0]
    assert w["1"] == [{"level": "orange", "event": "Gewitter", "from": "2027-05-24T10:00:00Z", "to": "2027-05-24T20:00:00Z",
                       "area": "Gießen", "source": "DWD"}]


def test_dwd_polygon_misses(monkeypatch):
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "germany")
    FEEDS["dwd"] = _dwd([_dwd_feat(12.0, 54.0)])
    assert weather.warnings_for(_stage([(50.4, 8.4, 0, 0), (50.8, 8.8, 4, 80)]))[0]["1"] == []


def test_fr_nuts_match(monkeypatch):
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "france")
    FEEDS["feeds-france"] = {"warnings": [_ma("orange", nuts="FR825", area="Var"), _ma("orange", nuts="FR523", area="Ille-et-Vilaine")]}
    # Route Castellane -> Draguignan (Var/Alpes-de-Haute-Provence)
    w = weather.warnings_for(_stage([(43.85, 6.51, 0, 0), (43.54, 6.46, 2, 50)]))[0]["1"]
    assert [x["area"] for x in w] == ["Var"] and w[0]["event"] == "Gewitter" and w[0]["source"] == "MeteoAlarm"


def test_other_country_only_orange_red(monkeypatch):
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "spain")
    FEEDS["feeds-spain"] = {"warnings": [_ma("yellow", area="Litoral de Barcelona"), _ma("red", area="Girona")]}
    w = weather.warnings_for(_stage([(41.4, 2.17, 0, 0), (42.0, 2.8, 2, 100)]))[0]["1"]
    assert [(x["level"], x["area"]) for x in w] == [("red", "Girona")]


def test_time_window_filter(monkeypatch):
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "germany")
    FEEDS["dwd"] = _dwd([_dwd_feat(8.5, 50.5, onset="2027-05-25T10:00:00Z", expires="2027-05-25T20:00:00Z")])  # Folgetag
    assert weather.warnings_for(_stage([(50.4, 8.4, 0, 0), (50.8, 8.8, 4, 80)]))[0]["1"] == []


def test_partial_on_failure(monkeypatch):
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "germany" if lat > 47 else "france")

    def boom(url, params=None):
        if "dwd" in url:
            raise weather.httpx.ConnectError("weg")
        return {"warnings": [_ma("orange", nuts="FR825")]}
    monkeypatch.setattr(weather, "_get_json", boom)
    w, errors = weather.warnings_for(_stage([(50.4, 8.4, 0, 0), (43.54, 6.46, 8, 900)]))
    assert "dwd" in errors and w["1"] and w["1"][0]["source"] == "MeteoAlarm"


def test_cache_10min(setup, monkeypatch):
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "spain")
    FEEDS["feeds-spain"] = {"warnings": []}
    weather.warnings_for(_stage([(41.4, 2.17, 0, 0)]))
    weather.warnings_for(_stage([(41.4, 2.17, 0, 0)]))
    assert len(setup) == 1


def test_dwd_bbox_lon_lat_order(monkeypatch):
    # maps.dwd.de liefert nur mit „minLon,minLat,maxLon,maxLat,EPSG:4326“ Treffer
    seen = {}

    def grab(url, params=None):
        seen.update(params or {})
        return _dwd([])
    monkeypatch.setattr(weather, "_get_json", grab)
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "germany")
    weather.warnings_for(_stage([(50.4, 8.4, 0, 0), (50.8, 8.8, 4, 80)]))
    lon0, lat0, lon1, lat1, crs = seen["bbox"].split(",")
    assert tuple(round(float(x), 3) for x in (lon0, lat0, lon1, lat1)) + (crs,) == (8.2, 50.2, 9.0, 51.0, "EPSG:4326")


# ---------------------------------------------------------------- Randfaelle
def test_country_skips_multi_country_extracts(monkeypatch):
    """Geofabrik-Katalog ist klein->gross sortiert: europe/alps vor europe/france – Castellane muss „france“ ergeben."""
    from shapely.geometry import box
    import regions
    idx = [{"region": "europe/alps", "geom": box(5.0, 43.5, 17.0, 48.5), "area": 1},
           {"region": "europe/france", "geom": box(-5.0, 41.0, 9.6, 51.2), "area": 2},
           {"region": "europe/czech-republic", "geom": box(12.0, 48.5, 18.9, 51.1), "area": 3}]
    monkeypatch.setattr(regions, "_load_index", lambda download=False: idx)
    assert weather._country_of(43.85, 6.51) == "france"
    assert weather._country_of(50.08, 14.43) == "czechia"
    assert weather._country_of(60.0, 30.0) is None


def test_feed_404_is_no_error(monkeypatch):
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "monaco")

    def nf(url, params=None):
        req = weather.httpx.Request("GET", url)
        raise weather.httpx.HTTPStatusError("404", request=req, response=weather.httpx.Response(404, request=req))
    monkeypatch.setattr(weather, "_get_json", nf)
    w, errors = weather.warnings_for(_stage([(43.73, 7.42, 0, 0)]))
    assert w["1"] == [] and errors == {}


def test_dwd_bbox_only_german_points_and_grouped(monkeypatch):
    seen = {}
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "germany" if lat > 47.5 else "france")

    def grab(url, params=None):
        if "dwd" in url:
            seen.update(params)
            return _dwd([_dwd_feat(8.3, 50.3) | {"properties": {**_dwd_feat(8.3, 50.3)["properties"], "AREADESC": f"Kreis {i}"}}
                         for i in range(5)])
        return {"warnings": []}
    monkeypatch.setattr(weather, "_get_json", grab)
    w, _ = weather.warnings_for(_stage([(50.4, 8.4, 0, 0), (50.8, 8.8, 4, 80), (43.5, 6.5, 9, 900)]))
    lon0, lat0, lon1, lat1, _crs = seen["bbox"].split(",")
    assert float(lat0) > 49 and float(lon1) < 10                      # Frankreich nicht in der Box
    assert seen["typeName"] == "dwd:Warnungen_Landkreise"
    assert len(w["1"]) == 1 and w["1"][0]["area"].startswith("Kreis 0, Kreis 1, Kreis 2") and "+2" in w["1"][0]["area"]


def test_meteoalarm_cancel_and_grouping(monkeypatch):
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "spain")
    cancel = _ma("red", area="Girona")
    cancel["alert"]["msgType"] = "Cancel"
    FEEDS["feeds-spain"] = {"warnings": [cancel] + [_ma("orange", area=f"Gebiet {i}") for i in range(4)]}
    w = weather.warnings_for(_stage([(41.4, 2.17, 0, 0), (42.0, 2.8, 2, 100)]))[0]["1"]
    assert len(w) == 1 and w[0]["level"] == "orange" and "+1" in w[0]["area"]


def test_feed_cache_is_bounded():
    weather._feed_cache.clear()
    for i in range(300):
        weather._store(("dwd", i), {"x": i})
    assert len(weather._feed_cache) <= weather.FEED_MAX


def test_failure_is_cached_briefly(monkeypatch):
    calls = []
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "spain")

    def slow_fail(url, params=None):
        calls.append(url)
        raise weather.httpx.ConnectTimeout("langsam")
    monkeypatch.setattr(weather, "_get_json", slow_fail)
    weather.warnings_for(_stage([(41.4, 2.17, 0, 0)]))
    weather.warnings_for(_stage([(41.4, 2.17, 0, 0)]))
    assert len(calls) == 1                                           # zweite Anfrage wartet nicht erneut


# ---------------------------------------------------------------- UK-Hochwasser an jeder Etappe
def test_no_local_hazards_without_borders(monkeypatch):
    """Ohne Gebietsgrenzen (z. B. UK) keine oertlichen Gefahren wie Kuesten-Hochwasser – die passen nicht zur Route."""
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "united-kingdom")
    FEEDS["feeds-united-kingdom"] = {"warnings": [
        _ma("orange", area="North Sea at Staithes", typ="12; flooding"),
        _ma("orange", area="Whitby Harbour", typ="7; coastalevent"),
        _ma("orange", area="Yorkshire", typ="1; Wind")]}
    w = weather.warnings_for(_stage([(54.0, -1.5, 0, 0), (55.0, -1.6, 3, 150)]))[0]["1"]
    assert [(x["event"], x["area"]) for x in w] == [("Wind", "Yorkshire")]


def test_same_warning_different_times_one_row(monkeypatch):
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "spain")
    FEEDS["feeds-spain"] = {"warnings": [
        _ma("orange", area=f"Gebiet {i}", onset=f"2027-05-24T1{i}:00:00+02:00", expires=f"2027-05-24T1{i}:30:00+02:00")
        for i in range(5)]}
    w = weather.warnings_for(_stage([(41.4, 2.17, 0, 0), (42.0, 2.8, 8, 100)]))[0]["1"]
    assert len(w) == 1
    assert w[0]["from"] == "2027-05-24T10:00:00+02:00" and w[0]["to"] == "2027-05-24T14:30:00+02:00"
    assert w[0]["area"] == "Gebiet 0, Gebiet 1, Gebiet 2 +2"


def test_level_case_insensitive(monkeypatch):
    """UK-Feed schreibt „2; Orange; Moderate“ – darf nicht verloren gehen."""
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "united-kingdom")
    FEEDS["feeds-united-kingdom"] = {"warnings": [_ma("Orange", area="Yorkshire", typ="1; Wind")]}
    w = weather.warnings_for(_stage([(54.0, -1.5, 0, 0), (55.0, -1.6, 3, 150)]))[0]["1"]
    assert [(x["level"], x["event"]) for x in w] == [("orange", "Wind")]


# ---------------------------------------------------------------- Grenzen fuer (fast) alle Laender
def test_emma_area_only_when_route_touches_it(monkeypatch):
    """Spanien nennt EMMA-Gebiete: Warnung nur, wenn ihr Gebiet die Strecke beruehrt – keine Kanaren auf dem Weg nach Barcelona."""
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "spain")
    FEEDS["feeds-spain"] = {"warnings": [
        _ma("yellow", emma="ES181", area="Litoral de Barcelona"),   # an der Strecke, auch gelb
        _ma("orange", emma="ES127", area="Oeste de La Palma"),      # Kanaren
        _ma("red", emma="ES139", area="Sur de Ávila"),               # Westspanien
    ]}
    w = weather.warnings_for(_stage([(41.55, 2.48, 0, 0), (41.40, 2.17, 1, 35)]))[0]["1"]
    assert [(x["level"], x["area"]) for x in w] == [("yellow", "Litoral de Barcelona")]


def test_cap_polygon_from_feed(monkeypatch):
    """Schweiz & Co. liefern Polygone im Feed (Liste von „lat,lon lat,lon …“)."""
    monkeypatch.setattr(weather, "_country_of", lambda lat, lon: "switzerland")
    poly = ["47.30,8.40 47.30,8.70 47.50,8.70 47.50,8.40 47.30,8.40"]
    far = ["46.00,7.00 46.00,7.10 46.10,7.10 46.00,7.00"]
    hit, miss = _ma("yellow", area="Zürich"), _ma("orange", area="Wallis")
    hit["alert"]["info"][0]["area"][0] = {"areaDesc": "Zürich", "polygon": poly}
    miss["alert"]["info"][0]["area"][0] = {"areaDesc": "Wallis", "polygon": far}
    FEEDS["feeds-switzerland"] = {"warnings": [hit, miss]}
    w = weather.warnings_for(_stage([(47.37, 8.54, 0, 0), (47.45, 8.60, 1, 15)]))[0]["1"]
    assert [x["area"] for x in w] == ["Zürich"]


def test_area_file_has_emma_and_nuts():
    assert weather._area_geom("EMMA_ID:ES183") is not None
    assert weather._area_geom("NUTS2:HU22") is not None
    assert weather._area_geom("EMMA_ID:XX999") is None
