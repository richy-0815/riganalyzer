
import osmdb
import rig
from test_build_db_camp import _build


def test_water_kind_rules():
    assert rig.water_kind({"amenity": "water_point"}, False) == "public"
    assert rig.water_kind({"amenity": "water_point"}, True) == "guests"
    assert rig.water_kind({"amenity": "water_point", "access": "customers"}, False) == "guests"
    assert rig.water_kind({"amenity": "drinking_water"}, False) == "tap"
    assert rig.water_kind({"amenity": "drinking_water", "access": "private"}, False) is None
    assert rig.water_kind({"tourism": "caravan_site", "water_point": "yes"}, False) == "guests"
    assert rig.water_kind({"tourism": "caravan_site"}, False) is None


def test_in_camp_inside_and_just_outside(tmp_path, monkeypatch):
    _build(tmp_path, monkeypatch)
    db = osmdb.OsmDB(str(tmp_path / "test.sqlite"))
    assert db.in_camp(43.8015, 6.5020) is True
    assert db.in_camp(43.8015, 6.50406) is False      # ~5 m ausserhalb der Ostkante
    assert db.in_camp(43.81, 6.52) is False


def test_fits_rig():
    assert rig.fits_rig({"maxlength": 12.0, "maxweight": None}, {"length": 12.5, "weight": 5}) == "no"
    assert rig.fits_rig({"maxlength": None, "maxweight": None}, {"length": 12.5, "weight": 5}) == "unknown"
    assert rig.fits_rig({"maxlength": 15.0, "maxweight": 7.5}, {"length": 12.5, "weight": 5}) == "ok"
    assert rig.fits_rig({"maxlength": 15.0, "maxweight": 3.5}, {"length": 12.5, "weight": 5}) == "no"
    assert rig.fits_rig({"maxlength": 15.0, "maxweight": None}, {"length": 12.5, "weight": 5}) == "unknown"


def test_poi_flags_normalized():
    f = rig.poi_flags({"tourism": "caravan_site", "fee": "no", "dog": "leashed", "shower": "yes", "toilets": "no",
                       "internet_access": "wlan", "maxweight": "7.5 t", "website": "https://x.example"}, "camp")
    assert f["fee"] is False and f["dog"] == "leashed" and f["shower"] is True and f["toilets"] is False
    assert f["wifi"] is True and f["maxweight"] == 7.5 and f["kind"] == "caravan_site" and f["website"] == "https://x.example"
    g = rig.poi_flags({"tourism": "camp_site"}, "camp")
    assert g["fee"] is None and g["dog"] is None and g["shower"] is None and g["maxweight"] is None
