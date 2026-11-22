"""Umweltzonen-Daten (lez.py): BNZFE + OSM holen, zusammenfuehren, Stand halten – nie echtes Netz."""

import datetime as dt
import glob
import json
import os

import pytest

import events
import lez
import weather

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def env(data_dir, monkeypatch):
    monkeypatch.setattr(lez, "DIR", str(data_dir / "lez"))
    monkeypatch.setattr(events, "DIR", str(data_dir / "events"))
    lez._reset_cache()

    def country(lat, lon):
        if lon < 0:
            return "spain"
        if lon < 6 and lat > 45:
            return "france"
        return "germany"
    monkeypatch.setattr(weather, "_country_of", country)
    return data_dir


def test_parse_bnzfe_rules():
    zs = {z["name"]: z for z in lez.parse_bnzfe(_load("lez_bnzfe.geojson"))}
    lyon = zs["Métropole de Lyon"]
    assert lyon["rule"] == {"kind": "critair", "vp": 2, "pl": 1, "vignette": True, "hours": None}
    assert lyon["since"] == "2026-01-01" and lyon["until"] is None
    assert lyon["id"] == "fr:200046977-ZFE-001" and lyon["source"] == "bnzfe" and lyon["country"] == "france"
    assert zs["Grenoble-Alpes-Métropole"]["rule"]["kind"] == "unknown"


def test_merge_prefers_bnzfe():
    fr = lez.parse_bnzfe(_load("lez_bnzfe.geojson"))
    osm = lez.parse_overpass(_load("lez_overpass.json"))
    merged = lez.merge(fr, osm)
    lyon = [z for z in merged if z["country"] == "france" and "Lyon" in z["name"]]
    assert len(lyon) == 1 and lyon[0]["source"] == "bnzfe"
    assert len(merged) == len(fr) + 2   # Stuttgart + Madrid bleiben


def test_critair_is_highest_allowed():
    assert [lez._critair(v) for v in ("V1", "V3", "V5", "NC", "EL", None, "x")] == [0, 2, 4, 5, 0, None, None]


def test_country_rules_from_table():
    zs = {z["name"]: z for z in lez.parse_overpass(_load("lez_overpass.json"))}
    assert zs["Umweltzone Stuttgart"]["rule"] == {"kind": "sticker", "need": "green"}
    assert zs["Madrid ZBE"]["rule"]["kind"] == "etiqueta"
    assert zs["Madrid ZBE"]["id"] == "osm:way/3003"
    assert zs["ZFE Lyon"]["id"] == "osm:relation/1001"
    assert zs["ZFE Lyon"]["geom"].area > 0   # Relation aus zwei Wegen zum Ring geschlossen


def test_rules_have_source_and_date():
    for land, e in lez.rules().items():
        assert e["source"].startswith("http"), land
        dt.date.fromisoformat(e["checked"])


def _fake_get(monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self): return _load("lez_bnzfe.geojson")
    monkeypatch.setattr(lez.httpx, "get", lambda *a, **k: R())
    monkeypatch.setattr(lez, "_fetch_osm", lambda: _load("lez_overpass.json"))


def test_refresh_keeps_last_on_failure(monkeypatch, env):
    _fake_get(monkeypatch)
    now = dt.datetime(2026, 11, 1, 12, 0)
    m = lez.refresh(now)
    assert m["count"] == 4 and m["errors"] == {} and m["bnzfe"] and m["osm"]
    assert len(lez.zones()) == 4 and lez.zones()[0]["geom"] is not None

    def boom(*a, **k):
        raise lez.httpx.ConnectError("weg")
    monkeypatch.setattr(lez.httpx, "get", boom)
    monkeypatch.setattr(lez, "_fetch_osm", lambda: (_ for _ in ()).throw(RuntimeError("overpass weg")))
    m2 = lez.refresh(now + dt.timedelta(days=7))
    assert set(m2["errors"]) == {"bnzfe", "osm"}
    assert m2["bnzfe"] == m["bnzfe"]            # Stand bleibt der alte
    assert len(lez.zones()) == 4
    assert set(lez.meta()["errors"]) == {"bnzfe", "osm"}
    kinds = [json.loads(line)["kind"] for f in glob.glob(str(env / "events" / "*.jsonl")) for line in open(f)]
    assert kinds.count("fehler") == 2


def test_refresh_drops_expired(monkeypatch):
    _fake_get(monkeypatch)
    d = _load("lez_bnzfe.geojson")
    d["features"][1]["properties"]["date_fin"] = "2020-12-31"
    monkeypatch.setattr(lez.httpx, "get", lambda *a, **k: type("R", (), {
        "raise_for_status": lambda s: None, "json": lambda s: d})())
    lez.refresh(dt.datetime(2026, 11, 1))
    assert not any(z["name"].startswith("Grenoble") for z in lez.zones())


def test_no_data_is_none():
    assert lez.zones() is None
    assert lez.meta() is None


def test_no_publish_until_both_sources_ok(monkeypatch):
    _fake_get(monkeypatch)
    monkeypatch.setattr(lez.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(lez.httpx.ConnectError("weg")))
    m = lez.refresh(dt.datetime(2026, 11, 1))
    assert lez.zones() is None and m["count"] == 0
    assert "bnzfe" in lez.meta()["errors"] and "osm" not in lez.meta()["errors"]
    _fake_get(monkeypatch)
    lez.refresh(dt.datetime(2026, 11, 8))
    assert len(lez.zones()) == 4


def test_corrupt_file_is_none(env):
    os.makedirs(lez.DIR)
    open(os.path.join(lez.DIR, "zones.geojson"), "w").write(json.dumps(
        {"type": "FeatureCollection", "features": [{"properties": {}, "geometry": {"type": "Polygon", "coordinates": 1}}]}))
    assert lez.zones() is None


def test_due_after_7_days():
    now = dt.datetime(2026, 10, 31, 12, tzinfo=dt.timezone.utc)

    def ago(d):
        return (now - dt.timedelta(days=d)).isoformat(timespec="seconds")
    assert lez.due(None, now) is True
    assert lez.due({"bnzfe": ago(6), "osm": ago(6)}, now) is False
    assert lez.due({"bnzfe": ago(8), "osm": ago(8)}, now) is True
    assert lez.due({"bnzfe": ago(1), "osm": ago(8)}, now) is True    # aelteste Quelle zaehlt
    assert lez.due({"bnzfe": ago(1), "osm": None}, now) is True      # nie geklappt = faellig
    assert lez.due({"bnzfe": ago(1)}, now) is True


@pytest.mark.parametrize("raw", [None, "", "  ", "24/7", "Mo-Su : 00:00-00:00", "Mo-Su 00:00-24:00", "00:00-24:00",
                                 "Mo-Su : 0:00-24:00", "00:00-00:00"])
def test_hours_allday_is_none(raw):
    assert lez._hours(raw) is None


@pytest.mark.parametrize("raw", ["Mo-Fr 07:00-9:00,16:00-19:00", "Mo-Fr 08:00-20:00; PH off", "Mo-Su 00:00-23:59; PH off"])
def test_hours_limited_kept(raw):
    assert lez._hours(raw) == raw


def _feat(zid, nom=None, pub=None, url=None):
    p = {"id": zid, "vp_critair": "V3", "vp_horaires": "Mo-Su : 00:00-00:00"}
    if nom is not None:
        p["nom"] = nom
    if url:
        p["url_site"] = url
    return {"type": "Feature", "properties": p, "publisher": pub,
            "geometry": {"type": "Polygon", "coordinates": [[[4, 45], [4.1, 45], [4.1, 45.1], [4, 45]]]}}


def test_bnzfe_names_human_and_allday_hours():
    fc = {"features": [
        _feat("200040715-ZFE-001", pub={"nom": "Grenoble-Alpes-Métropole"}),          # Kennung, Traeger vorhanden
        _feat("1", url="https://www.grandannecy.fr/zfem"),                            # kein publisher: Host
        _feat("2", nom="", pub=None),                                                 # nichts bekannt
        _feat("200067213-ZFE-001", nom="ZFEM", pub={"nom": "CU du Grand Reims"}),     # echter nom bleibt
        _feat("3", nom="200040715-ZFE-001", pub={"nom": "Dijon Métropole"}),          # nom ist selbst Kennung
    ]}
    zs = lez.parse_bnzfe(fc)
    assert [z["name"] for z in zs] == ["Grenoble-Alpes-Métropole", "Grand Annecy", "Umweltzone", "ZFEM", "Dijon Métropole"]
    assert all(z["rule"]["hours"] is None for z in zs)
