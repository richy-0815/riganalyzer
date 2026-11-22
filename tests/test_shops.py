import math
import os
import shutil
import sqlite3

import pytest

from app import shops, shops_build

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "shops.osm")
KX = 111320.0 * 0.6293  # m je Grad Laenge bei 51 N (grob)


def _line(dm):
    """Senkrechte Linie dm Meter oestlich vom Lidl (n10), von Sued nach Nord."""
    lon = 10.0100 + dm / KX
    return [(50.95 + i * 0.01, lon) for i in range(12)]


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(shops, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(shops, "_db", None)
    monkeypatch.setattr(shops, "_db_key", None)
    os.makedirs(tmp_path / "shops")
    dst = str(tmp_path / "shops" / "shops-de.sqlite")
    shops_build.build(FIX, dst)
    return dst


def test_near_within_and_outside(data):
    db = shops.get()
    near = {s["osm"]: s for s in db.near(_line(1500))}
    assert "n10" in near
    s = near["n10"]
    assert set(s) == {"osm", "lat", "lon", "kind", "name", "brand", "hours", "addr", "extra", "off", "km"}
    assert s["kind"] == "discount" and s["name"] == "Lidl" and isinstance(s["off"], int)
    assert 1400 <= s["off"] <= 1600
    assert "n10" not in {x["osm"] for x in db.near(_line(2500))}


def test_km_monotonic(data):
    res = shops.get().near(_line(1500), max_m=5000)
    assert len(res) >= 3
    kms = [s["km"] for s in res]
    assert kms == sorted(kms)
    assert all(isinstance(k, float) for k in kms)


def test_degenerate_lines(data):
    db = shops.get()
    assert db.near([]) == []
    assert db.near([(51.0, 10.0)]) == []


def test_missing_and_corrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(shops, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(shops, "_db", None)
    monkeypatch.setattr(shops, "_db_key", None)
    assert shops.get() is None
    os.makedirs(tmp_path / "shops")
    (tmp_path / "shops" / "shops-de.sqlite").write_bytes(b"kein sqlite" * 50)
    assert shops.get() is None


def test_reload_on_mtime(data):
    assert shops.get().meta["count"] == "5"
    tmp = data + ".x"
    shutil.copy(data, tmp)
    c = sqlite3.connect(tmp)
    c.execute("DELETE FROM shop WHERE id > 2")
    c.execute("UPDATE meta SET value='2' WHERE key='count'")
    c.commit()
    c.close()
    os.replace(tmp, data)
    os.utime(data, (os.path.getmtime(data) + 10,) * 2)
    assert shops.get().meta["count"] == "2"


def test_touches_germany():
    assert shops.touches_germany([(52.52, 13.40), (48.14, 11.58)])
    assert not shops.touches_germany([(48.21, 16.37), (47.07, 15.44)])
    assert not shops.touches_germany([])


def _mk(tmp_path, monkeypatch, osm_text):
    monkeypatch.setattr(shops, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(shops, "_db", None)
    monkeypatch.setattr(shops, "_db_key", None)
    os.makedirs(tmp_path / "shops")
    src = tmp_path / "in.osm"
    src.write_text(osm_text)
    shops_build.build(str(src), str(tmp_path / "shops" / "shops-de.sqlite"))


def _node(i, lat, lon):
    return (f'<node id="{i}" lat="{lat:.6f}" lon="{lon:.6f}" version="1"><tag k="shop" v="bakery"/>'
            f'<tag k="name" v="B{i}"/></node>')


@pytest.mark.parametrize("sparse", [False, True])
def test_long_ns_route_local_scale(tmp_path, monkeypatch, sparse):
    lon = 10.0
    ends = {"fl": 54.8, "ga": 47.5}
    nodes = []
    for tag, la in ends.items():
        dlon = lambda m, la=la: m / (111320.0 * math.cos(math.radians(la)))  # noqa: E731
        nodes += [_node(1 if tag == "fl" else 3, la, lon + dlon(1900)), _node(2 if tag == "fl" else 4, la, lon + dlon(2100))]
    _mk(tmp_path, monkeypatch, '<?xml version="1.0"?><osm version="0.6">' + "".join(nodes) + "</osm>")
    n = 731
    line = [(54.8 - i * (7.3 / (n - 1)), lon) for i in range(n)]
    if sparse:
        line = [line[0], line[-1]]
    res = {s["osm"]: s for s in shops.get().near(line)}
    assert set(res) == {"n1", "n3"}
    assert abs(res["n1"]["off"] - 1900) < 15 and abs(res["n3"]["off"] - 1900) < 15
    assert abs(res["n1"]["km"] - 0.0) < 0.1
    assert abs(res["n3"]["km"] - 7.3 * 110.574) < 0.1


def test_de_to_at_route(data):
    line = [(51.0, 10.0), (50.0, 12.0), (48.2, 16.4)]
    res = shops.get().near(line, max_m=100000)
    assert res and all(s["osm"].startswith(("n", "w")) for s in res)


def test_file_deleted_after_load(data):
    assert shops.get() is not None
    os.remove(data)
    assert shops.get() is None


def test_osmdb_ignores_stray_shops_file(tmp_path, monkeypatch):
    import osmdb
    d = tmp_path / "osm"
    d.mkdir()
    shops_build.build(FIX, str(d / "shops-de.sqlite"))   # k/value-Meta, keine Kartenregion
    (d / "andere.sqlite").write_bytes(b"")
    assert [os.path.basename(f) for f in osmdb.db_files(legacy=str(tmp_path / "nix"), region_dir=str(d))] == ["andere.sqlite"]
    # auch ein unbrauchbares Meta-Schema bricht get() nicht
    monkeypatch.setenv("OSM_DIR", str(d))
    monkeypatch.setenv("OSM_DB", str(tmp_path / "nix"))
    shutil.copy(str(d / "shops-de.sqlite"), str(d / "kaputt.sqlite"))
    monkeypatch.setattr(osmdb, "_db", None)
    monkeypatch.setattr(osmdb, "_db_key", None)
    assert osmdb.get() is None


def test_route_pois_service_is_own_group(monkeypatch):
    """0.47.0 (#4): Werkstatt/Gas/Waschsalon als Gruppe „service“ – „Einkaufen“ und „letzter Einkauf“ bleiben Laeden."""
    import numpy as np

    class Fake:
        def near(self, line):
            return [{"km": 3.0, "lat": 52.0, "lon": 13.0, "off": 120, "kind": "gas", "name": "", "brand": "", "hours": "", "addr": "",
                     "osm": "n1"},
                    {"km": 5.0, "lat": 52.1, "lon": 13.1, "off": 80, "kind": "discount", "name": "Lidl", "brand": "Lidl", "hours": "",
                     "addr": "", "osm": "n2"}]
    monkeypatch.setattr(shops, "get", lambda: Fake())
    out = shops.route_pois(np.array([52.0, 52.2]), np.array([13.0, 13.2]), [])
    assert [(p["group"], p["label"], p["name"]) for p in out] == [("service", "Gasflaschen", "Gasflaschen"), ("shop", "Discounter", "Lidl")]


def test_route_pois_detailkarte_bekommt_kontakt_und_leistungen(monkeypatch):
    """0.54.0 (#31): Telefon, Website, Leistungen aus der extra-Spalte landen in flags."""
    import numpy as np

    class Fake:
        def near(self, line):
            return [{"km": 3.0, "lat": 52.0, "lon": 13.0, "off": 120, "kind": "caravan", "name": "Camper Service", "brand": "", "hours": "",
                     "addr": "", "osm": "n1", "extra": {"phone": "+49 30 123456", "services": ["gas", "motorhome"]}}]
    monkeypatch.setattr(shops, "get", lambda: Fake())
    f = shops.route_pois(np.array([52.0, 52.2]), np.array([13.0, 13.2]), [])[0]["flags"]
    assert f["phone"] == "+49 30 123456" and f["services"] == ["gas", "motorhome"] and f["kind"] == "caravan"


def test_alter_datensatz_ohne_extra_spalte(data):
    """Bis zum Neubau des Datensatzes: alte Tabelle ohne extra geht weiter, extra ist dann leer."""
    db = sqlite3.connect(data)
    db.execute("CREATE TABLE shop_alt AS SELECT id, osm, lat, lon, kind, name, brand, hours, addr FROM shop")
    db.execute("DROP TABLE shop")
    db.execute("ALTER TABLE shop_alt RENAME TO shop")
    db.commit()
    db.close()
    sdb = shops.ShopDB(data)
    assert sdb.has_extra is False
    hits = sdb.near(_line(300))
    assert hits and all(h["extra"] == {} for h in hits)
