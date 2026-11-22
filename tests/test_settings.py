"""Konto-Einstellungen am Login (seit v0.10): speed_warn fuer RigNavi; Fahrzeug tempo100."""
from test_admin import ORIGIN, basic, env, login  # noqa: F401  (Fixture env)

import trips


def test_default_on(env):  # noqa: F811 – Fixture (Import oben)
    assert env.get("/api/me", headers=basic("olaf")).json()["settings"]["speed_warn"] is True


def test_set_and_read(env):  # noqa: F811 – Fixture (Import oben)
    r = env.post("/api/me/settings", json={"speed_warn": False}, headers=basic("olaf"))
    assert r.status_code == 200 and r.json()["settings"]["speed_warn"] is False
    assert env.get("/api/me", headers=basic("olaf")).json()["settings"]["speed_warn"] is False


def test_csrf_required_for_cookie(env):  # noqa: F811 – Fixture (Import oben)
    h = login(env, "olaf")
    assert env.post("/api/me/settings", json={"speed_warn": False}, headers={**h, "x-ra-csrf": "falsch"}).status_code == 403
    assert env.post("/api/me/settings", json={"speed_warn": False}, headers=h).status_code == 200


def test_unknown_key_422(env):  # noqa: F811 – Fixture (Import oben)
    assert env.post("/api/me/settings", json={"admin": True}, headers=basic("olaf")).status_code == 422
    assert env.post("/api/me/settings", json={"speed_warn": "ja"}, headers=basic("olaf")).status_code == 422


def test_setting_is_per_user(env):  # noqa: F811 – Fixture (Import oben)
    env.post("/api/me/settings", json={"speed_warn": False}, headers=basic("olaf"))
    assert env.get("/api/me", headers=basic("bofh")).json()["settings"]["speed_warn"] is True


def test_trip_vehicle_tempo100():
    assert trips._vehicle({})["tempo100"] is False
    assert trips._vehicle({"trailer": True, "tempo100": True})["tempo100"] is True


def test_lez_defaults(env):  # noqa: F811 – Fixture (Import oben)
    s = env.get("/api/me", headers=basic("olaf")).json()["settings"]
    assert s == {"speed_warn": True, "lez_euro": 6, "lez_fuel": "diesel",
                 "critair_vignette": False, "lez_registered": False}


def test_settings_lez_types(env):  # noqa: F811 – Fixture (Import oben)
    h = basic("olaf")
    r = env.post("/api/me/settings", json={"lez_euro": 6, "lez_fuel": "diesel", "critair_vignette": True,
                                           "lez_registered": True}, headers=h)
    assert r.status_code == 200 and r.json()["settings"]["lez_euro"] == 6
    for bad in ({"lez_euro": 9}, {"lez_euro": -1}, {"lez_euro": True}, {"lez_euro": "6"},
                {"lez_fuel": "kerosin"}, {"critair_vignette": "ja"}, {"lez_registered": 1}, {"speed_warn": 1}):
        assert env.post("/api/me/settings", json=bad, headers=h).status_code == 422, bad
