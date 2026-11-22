import os
import sqlite3

import pytest

import shops_build

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "shops.osm")


@pytest.mark.parametrize("tags,kind", [
    ({"shop": "supermarket", "brand": "Lidl"}, "discount"),
    ({"shop": "supermarket", "brand": "ALDI SÜD"}, "discount"),
    ({"shop": "supermarket", "name": "Netto Marken-Discount"}, "discount"),
    ({"shop": "supermarket", "brand": "EDEKA"}, "supermarket"),
    ({"shop": "supermarket"}, "supermarket"),
    ({"shop": "bakery"}, "bakery"),
    ({"shop": "butcher"}, "butcher"),
    ({"shop": "farm"}, "farm"),
    ({"shop": "clothes"}, None),
    # Service unterwegs
    ({"shop": "car_repair"}, "repair"),
    ({"shop": "truck_repair"}, "truck_repair"),
    ({"shop": "caravan"}, "caravan"),
    ({"shop": "gas"}, "gas"),
    ({"shop": "laundry"}, "laundry"),
    ({"shop": "laundry", "self_service": "no"}, None),
    ({"amenity": "cafe"}, None),
])
def test_classify(tags, kind):
    assert shops_build.classify(tags) == kind


def test_build(tmp_path):
    dst = str(tmp_path / "shops-de.sqlite")
    assert shops_build.build(FIX, dst) == 5
    assert not os.path.exists(dst + ".tmp")
    db = sqlite3.connect(dst)
    assert db.execute("SELECT value FROM meta WHERE key='count'").fetchone()[0] == "5"
    assert db.execute("SELECT value FROM meta WHERE key='built'").fetchone()[0]
    kinds = dict(db.execute("SELECT name, kind FROM shop").fetchall())
    assert kinds["Netto Marken-Discount"] == "discount"
    assert "Mode Test" not in kinds
    row = db.execute("SELECT lat, lon FROM shop WHERE osm='w100'").fetchone()
    assert 51.0 < row[0] < 51.002 and 10.0 < row[1] < 10.004
    hit = db.execute(
        "SELECT s.name, s.addr, s.hours FROM shop_ix i JOIN shop s ON s.id=i.id "
        "WHERE i.minlon>=9.99 AND i.maxlon<=10.02 AND i.minlat>=51.005 AND i.maxlat<=51.015").fetchall()
    assert hit == [("Lidl", "Teststrasse 1, 99999 Testdorf", "Mo-Sa 07:00-21:00")]


def test_build_source_date(tmp_path):
    dst = str(tmp_path / "shops-de.sqlite")
    shops_build.build(FIX, dst)
    sd = sqlite3.connect(dst).execute("SELECT value FROM meta WHERE key='source_date'").fetchone()[0]
    assert len(sd) == 10 and sd[4] == "-" and sd[7] == "-"


def test_source_date_falls_back_to_mtime(tmp_path):
    f = tmp_path / "x.osm"
    f.write_text("nicht lesbar")
    os.utime(f, (1_700_000_000, 1_700_000_000))
    assert shops_build._source_date(str(f)) == "2023-11-14"


def test_extra_kontakt_und_leistungen():
    """0.54.0 (#31): nur gesetzte Felder, Leistungen aus service:vehicle:*=yes sortiert."""
    import json
    tg = {"shop": "car_repair", "contact:phone": "+49 1", "website": "https://w.de", "service:vehicle:tyres": "yes",
          "service:vehicle:caravan": "yes", "service:vehicle:glass": "no", "operator": "Meier GmbH"}
    assert json.loads(shops_build.extra(tg)) == {"phone": "+49 1", "website": "https://w.de", "operator": "Meier GmbH",
                                                 "services": ["caravan", "tyres"]}
    assert json.loads(shops_build.extra({"shop": "gas"})) == {"gas_exchange": True}
    assert shops_build.extra({"shop": "bakery"}) is None
