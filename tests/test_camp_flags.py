"""Platz-Infos aus OSM (seit v0.24.0): mehr Tags, Hoehe und „keine Wohnmobile“ in fits_rig."""
import rig

VEH = {"length": 7.4, "weight": 3.5, "height": 3.1}


def test_more_osm_tags():
    f = rig.poi_flags({"tourism": "caravan_site", "maxheight": "2.8", "surface": "gravel", "stars": "3",
                       "reservation": "required", "contact:email": "info@aire.fr", "check_date": "2025-06-14",
                       "description": "x" * 400, "image": "http://unsicher.example/a.jpg", "motorhome": "yes",
                       "addr:city": "Castellane"}, "camp")
    assert f["maxheight"] == 2.8 and f["surface"] == "gravel" and f["stars"] == "3" and f["reservation"] == "required"
    assert f["email"] == "info@aire.fr" and f["check_date"] == "2025-06-14" and len(f["description"]) == 400
    assert f["image"] == "" and f["motorhome"] is True and f["city"] == "Castellane"


def test_fits_rig_height_and_no_motorhomes():
    assert rig.fits_rig({"maxlength": 10, "maxweight": 7.5, "maxheight": 2.8}, VEH) == "no"
    assert rig.fits_rig({"maxlength": 10, "maxweight": 7.5, "maxheight": 3.5}, VEH) == "ok"
    assert rig.fits_rig({"motorhome": False}, VEH) == "no"
    assert rig.fits_rig({}, VEH) == "unknown"
