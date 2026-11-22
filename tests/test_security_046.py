"""Sicherheits-Release 0.46.1 (Review 13.11.2026): Bremse zaehlt vor dem Pruefen, 2FA-Sperre je Login,
Daten geloeschter Logins, Registrierung ohne Auskunft, Regionen nur fuer Admins, Mengen je Login."""
import asyncio
import json

import bcrypt
import pytest

import auth

RIG = "https://riganalyzer.petrick.top"
RIDE = "https://rideanalyzer.petrick.top"
ORIGIN = {"origin": RIG, "user-agent": "Mozilla/5.0"}
PW = "geheim12345"


@pytest.fixture
def users(data_dir, monkeypatch):
    h = bcrypt.hashpw(PW.encode(), bcrypt.gensalt(rounds=4)).decode()
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True, "mail": "bofh@example.org"},
        "olaf": {"hash": h, "apps": ["rig"], "mail": "olaf@example.org"},
        "rita": {"hash": h, "apps": ["ride"]},
    }}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    auth._user_fails.clear()
    import main
    import register
    register._ip_hits.clear()
    register._ip_tries.clear()
    monkeypatch.setattr(main.mailer, "send", lambda *a, **k: True)
    return data_dir


def _client(host=RIG):
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app, base_url=host)


def _login(c, user, host=RIG):
    o = {"origin": host, "user-agent": "Mozilla/5.0"}
    r = c.post("/login", data={"user": user, "password": PW}, headers=o, follow_redirects=False)
    assert r.status_code == 303, r.text
    return {**o, "x-ra-csrf": c.cookies.get("ra_csrf") or ""}


async def _parallel(n, make):
    import httpx
    import main
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=RIG) as ac:
        rs = await asyncio.gather(*[make(ac, i) for i in range(n)])
    return [r.status_code for r in rs]


# ---------------------------------------------------------------- Bremse

def test_parallel_wrong_logins_hit_the_limit(users):
    hdr = {**ORIGIN, "x-forwarded-for": "1.2.3.4"}
    codes = asyncio.run(_parallel(40, lambda ac, i: ac.post("/login", data={"user": "olaf", "password": f"falsch{i}xx"}, headers=hdr)))
    assert codes.count(401) <= auth.MAX_FAILS
    assert codes.count(429) >= 40 - auth.MAX_FAILS


def test_successful_logins_do_not_count(users):
    c = _client()
    for _ in range(auth.MAX_FAILS + 5):
        _login(c, "olaf")
    assert not auth.throttled("testclient")


def test_basic_auth_success_does_not_count(users):
    import base64
    hdr = {"authorization": "Basic " + base64.b64encode(f"olaf:{PW}".encode()).decode(), "user-agent": "okhttp/4"}
    c = _client()
    for _ in range(auth.MAX_FAILS + 5):
        assert c.get("/api/trips", headers=hdr).status_code == 200


def test_totp_limited_per_login_across_ips(users):
    import twofa
    c = _client()
    pend = twofa.make_pending("bofh", "rig", False)
    codes = []
    for i in range(auth.MAX_USER_FAILS + 3):   # jede Anfrage von einer anderen IP
        c.cookies.set(twofa.PEND_COOKIE, pend)
        r = c.post("/zweiter-faktor", data={"code": "000000"}, headers={**ORIGIN, "x-forwarded-for": f"10.9.0.{i}"},
                   follow_redirects=False)
        codes.append(r.status_code)
    assert codes[:auth.MAX_USER_FAILS] == [401] * auth.MAX_USER_FAILS
    assert set(codes[auth.MAX_USER_FAILS:]) == {429}


# ---------------------------------------------------------------- geloeschte Logins

STOPS = [{"lat": 52.5, "lon": 13.4, "name": "Berlin"}, {"lat": 51.7, "lon": 14.3, "name": "Cottbus"}]


def test_deleted_login_leaves_nothing_for_a_new_owner(users):
    import admin
    import feedback
    import purge
    import register
    import trips
    t = trips.save("olaf", {"name": "Geheimtour", "stops": STOPS, "note": "privat"})
    assert t
    (users / "prefs").mkdir(exist_ok=True)
    (users / "prefs" / "olaf.json").write_text("{}")
    feedback.add("olaf", "rig", "idee", "Bitte mehr Stellplätze")
    admin.delete("olaf")
    weg = purge.purge_user("olaf")
    assert "Touren" in weg and "Einstellungen" in weg
    register.create("olaf", "fremd@example.org", "fremdes12345", "fremdes12345", "", "rig")
    admin.update("olaf", locked=False)
    assert trips.list_("olaf") == []
    assert not (users / "prefs" / "olaf.json").exists()
    assert all(x["user"] != "olaf" for x in feedback._load())
    assert any(x["user"] == purge.gone("olaf") for x in feedback._load())


def test_admin_delete_endpoint_purges(users, monkeypatch):
    import trips
    from conftest import enable_admin_2fa
    trips.save("olaf", {"name": "Tour", "stops": STOPS})
    c = _client()
    h = _login(c, "bofh")
    enable_admin_2fa(c, h)
    h["x-ra-csrf"] = c.cookies.get("ra_csrf")
    r = c.delete("/api/admin/users/olaf", headers=h)
    assert r.status_code == 204, r.text
    assert not (users / "trips" / "olaf").exists()


# ---------------------------------------------------------------- Registrieren

def _reg(c, user, mail, ip="5.6.7.8"):
    return c.post("/registrieren", data={"user": user, "mail": mail, "pw": "abcdefghijk1", "pw2": "abcdefghijk1"},
                  headers={**ORIGIN, "x-forwarded-for": ip})


def test_register_does_not_reveal_existing_mail(users, monkeypatch):
    import main
    sent = []
    monkeypatch.setattr(main.mailer, "send", lambda to, subj, body, *a, **k: sent.append((to, subj, body)) or True)
    monkeypatch.setattr(main, "_in_background", lambda fn, *a, **k: fn(*a, **k))
    c = _client()
    r = _reg(c, "neuling", "olaf@example.org")
    assert r.status_code == 200 and "beantragt" in r.text
    assert "neuling" not in auth.read_disk()
    assert any(to == "olaf@example.org" and "olaf" in body for to, _s, body in sent)


def test_register_attempts_are_limited_per_ip(users):
    import register
    c = _client()
    codes = [_reg(c, "olaf", f"x{i}@example.org").status_code for i in range(register.ATTEMPT_MAX + 3)]
    assert codes[:register.ATTEMPT_MAX] == [422] * register.ATTEMPT_MAX
    assert set(codes[register.ATTEMPT_MAX:]) == {429}


def test_register_hashes_only_after_checks(users, monkeypatch):
    import register
    calls = []
    monkeypatch.setattr(auth, "hash_pw", lambda pw: calls.append(pw) or "x")
    with pytest.raises(register.RegisterError):
        register.create("olaf", "neu@example.org", "abcdefghijk1", "abcdefghijk1", "", "rig")
    assert calls == []


def test_mail_with_comma_or_brackets_rejected():
    import re

    import admin
    for bad in ("telegram,ich@evil.example", "a;b@c.de", "x <y@z.de>", 'a"b@c.de'):
        assert not re.match(admin.MAIL_RE, bad), bad
    for ok in ("a@b.de", "vor.nach+tag@ex-ample.org"):
        assert re.match(admin.MAIL_RE, ok), ok


# ---------------------------------------------------------------- Regionen, Ortssuche, Mengen

def test_regions_only_for_top_admins(users):
    c = _client()
    h = _login(c, "olaf")
    r = c.post("/api/regions", json={"region": "europe/andorra"}, headers=h)
    assert r.status_code == 403 and "Rückmeldung" in r.text


def test_regions_admin_ohne_eigenen_bau(users, monkeypatch):
    """0.46.12: nach der Trennung baut nur der Baurechner (OSM_AUTOBUILD=0) – die Bestellung meldet das statt Erfolg."""
    from conftest import enable_admin_2fa
    monkeypatch.setenv("OSM_AUTOBUILD", "0")
    c = _client()
    h = _login(c, "bofh")
    enable_admin_2fa(c, h)
    h["x-ra-csrf"] = c.cookies.get("ra_csrf")
    r = c.post("/api/regions", json={"region": "europe/andorra"}, headers=h)
    assert r.status_code == 409 and "Baurechner" in r.text


def test_geocode_limited_per_login(users, monkeypatch):
    import main
    import planner
    main._geo_hits.clear()
    monkeypatch.setattr(planner, "geocode", lambda q: [])
    c = _client()
    h = _login(c, "olaf")
    codes = [c.get(f"/api/geocode?q=ort{i}", headers=h).status_code for i in range(main.GEO_PER_MIN + 2)]
    assert codes[:main.GEO_PER_MIN] == [200] * main.GEO_PER_MIN
    assert codes[-1] == 429


def test_geocode_does_not_queue_forever(monkeypatch):
    import threading

    import planner
    monkeypatch.setattr(planner, "GEO_QUEUE_S", 0.05)
    lock = planner.__dict__.setdefault("_geo_lock", threading.Lock())
    lock.acquire()
    try:
        with pytest.raises(planner.GeoBusy):
            planner.geocode("irgendwo-unbekannt-xyz")
    finally:
        lock.release()


def test_trip_count_limited(users, monkeypatch):
    import trips
    monkeypatch.setattr(trips, "MAX_TRIPS", 3)
    for i in range(3):
        trips.save("olaf", {"name": f"T{i}", "stops": STOPS})
    with pytest.raises(trips.TripError):
        trips.save("olaf", {"name": "zu viel", "stops": STOPS})
    t = trips.list_("olaf")[0]
    trips.save("olaf", {"id": t["id"], "name": "geaendert", "stops": STOPS})   # aendern geht weiter


def test_package_queue_limited_per_login(monkeypatch):
    import package
    monkeypatch.setattr(package, "_pending", {("olaf", "a" * 12), ("olaf", "b" * 12)})
    with pytest.raises(package.TooMany):
        package.request("olaf", "c" * 12)


# ---------------------------------------------------------------- Rig-Router

RIG_ONLY = [("post", "/api/fuel/prices"), ("post", "/api/camps/fr"), ("post", "/api/reviews/lookup"), ("get", "/api/reviews"),
            ("post", "/api/reviews"), ("delete", "/api/reviews"), ("get", "/api/camps/photo"), ("get", "/api/speedrules"),
            ("get", "/app/rignavi.apk"), ("post", "/api/nav/pois"), ("post", "/api/nav/incidents"), ("post", "/api/nav/ping"),
            ("post", "/api/tours"), ("get", "/api/trips"), ("get", "/api/shares")]


def test_rig_endpoints_do_not_exist_on_ride(users):
    """Auch reine Ride-Logins kamen vorher an Bewertungen, Plaetze, Spritpreise, APK … (Review 13.11.2026)."""
    c = _client(RIDE)
    h = _login(c, "rita", RIDE)
    for method, path in RIG_ONLY:
        r = getattr(c, method)(path, headers=h)
        assert r.status_code == 404, (method, path, r.status_code)


def test_rig_router_covers_the_rig_endpoints():
    import main  # noqa: F401 – Router erst nach dem App-Start eingehaengt
    import rig_routes
    rig_paths = {r.path for r in rig_routes.rig.routes}
    for _m, path in RIG_ONLY:
        assert path in rig_paths, path
    assert "/api/nav/route" not in rig_paths   # Navi-Route gibt es fuer beide (Ride ohne Umweltzonen)


def test_page_scripts_paths():
    """0.46.3: App-eigene Skripte aus static-ride/ bzw. static-rig/, alles andere (auch vendor/…) aus static/."""
    import main
    html = main._page(True, "T", "i", "", scripts=("vendor/leaflet/leaflet.js", "admin.js", "static-rig/teilen.js"))
    assert 'src="/static/vendor/leaflet/leaflet.js?v=' in html
    assert 'src="/static/admin.js?v=' in html
    assert 'src="/static-rig/teilen.js?v=' in html
