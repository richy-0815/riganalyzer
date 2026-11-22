"""Admin 0.14.1: 2FA-Pflicht schon bei der Anmeldung, Gruppen-Admin nur mit Gruppe, „Letzte Änderungen“."""

from test_group_admin import ORIGIN, env, login  # noqa: F401  (Fixture env)


def plain_login(user):
    from fastapi.testclient import TestClient
    import main
    from test_group_admin import HOST
    c = TestClient(main.app, base_url=HOST)
    r = c.post("/login", data={"user": user, "password": "geheim12345"}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


def test_admin_without_2fa_must_set_it_up_first(env):  # noqa: F811 – Fixture (Import oben)
    c = plain_login("gina")
    r = c.get("/", headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/2fa-einrichten"
    assert c.get("/api/trips", headers=ORIGIN).status_code == 403
    assert c.get("/2fa-einrichten", headers=ORIGIN).status_code == 200
    assert c.get("/logout", headers=ORIGIN, follow_redirects=False).status_code in (200, 303)


def test_admin_with_2fa_and_normal_user_unaffected(env):  # noqa: F811 – Fixture (Import oben)
    c, _ = login("gina")
    assert c.get("/", headers=ORIGIN, follow_redirects=False).status_code == 200
    c = plain_login("otto")
    assert c.get("/", headers=ORIGIN, follow_redirects=False).status_code == 200


def test_group_admin_needs_a_group(env):  # noqa: F811 – Fixture (Import oben)
    c, h = login()
    r = c.patch("/api/admin/users/olaf", json={"admin": {"apps": ["ride"], "groups": []}}, headers=h)
    assert r.status_code == 422 and "Gruppe" in r.json()["detail"]


def test_change_log(env):  # noqa: F811 – Fixture (Import oben)
    c, h = login()
    c.patch("/api/admin/users/mia", json={"mail": "mia@example.org"}, headers=h)
    c.patch("/api/admin/users/olaf", json={"admin": {"apps": ["ride"], "groups": ["g2"]}}, headers=h)
    c.post("/api/admin/groups", json={"name": "Alpen"}, headers=h)
    log = c.get("/api/admin/log", headers=h).json()
    assert [(e["who"], e["target"]) for e in log[:3]] == [("bofh", "Alpen"), ("bofh", "olaf"), ("bofh", "mia")]
    assert "Ride" in log[1]["what"] and "Womo" in log[1]["what"] and log[0]["t"] >= log[2]["t"]
    g, gh = login("gina")
    g.post("/api/admin/users/otto/password", json={}, headers=gh)
    mine = g.get("/api/admin/log", headers=gh).json()
    assert {e["target"] for e in mine} == {"mia", "otto"}          # olaf/Gruppe gehen sie nichts an
    assert all("password" not in e["what"].lower() or "Passwort" in e["what"] for e in mine)
