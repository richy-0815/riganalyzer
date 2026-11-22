"""0.35.0: Rueckmeldung aus der App – nur angemeldet, Telegram (Alarm-Kanal), Liste im Admin je App, Bremse 10/Tag."""
import json

import pytest

import auth
import feedback

RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"


@pytest.fixture
def env(data_dir, monkeypatch):
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True},
        "olaf": {"hash": h, "apps": ["rig"], "mail": "olaf@example.org"},
        "otto": {"hash": h, "apps": ["ride"]},
        "mia": {"hash": h, "apps": ["ride", "rig"], "admin": {"apps": ["ride", "rig"], "groups": ["g1"]}, "group": "g1"}}}))
    (data_dir / "groups.json").write_text(json.dumps({"groups": {"g1": {"name": "XJR"}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    sent = []
    monkeypatch.setattr(feedback.mailer, "send", lambda to, s, b: sent.append((to, s, b)) or True)
    monkeypatch.setattr(feedback, "_async", lambda fn, *a: fn(*a))
    return sent


def user_client(host, user):
    from fastapi.testclient import TestClient
    import main
    o = {"origin": host, "user-agent": "Mozilla/5.0"}
    c = TestClient(main.app, base_url=host)
    c.post("/login", data={"user": user, "password": "geheim12345"}, headers=o, follow_redirects=False)
    return c, {**o, "x-ra-csrf": c.cookies.get("ra_csrf")}


def test_send_feedback_notifies_lisa_alerts(env):
    c, h = user_client(RIG, "olaf")
    r = c.post("/api/feedback", json={"kind": "idee", "text": "Bitte Fähren anzeigen", "page": "/"}, headers=h)
    assert r.status_code == 200
    (to, subj, body), = env
    assert to == "telegram@app.internal" and "olaf" in subj and "RigAnalyzer" in subj and "Idee" in subj
    assert "Bitte Fähren anzeigen" in body and "olaf@example.org" in body
    assert feedback.items("rig")[0]["text"] == "Bitte Fähren anzeigen"


def test_validation_and_auth(env):
    c, h = user_client(RIDE, "otto")
    assert c.post("/api/feedback", json={"kind": "idee", "text": "x"}, headers=h).status_code == 422           # zu kurz
    assert c.post("/api/feedback", json={"kind": "spam", "text": "Hallo Welt"}, headers=h).status_code == 422
    assert c.post("/api/feedback", json={"kind": "lob", "text": "a" * 4001}, headers=h).status_code == 422
    assert c.post("/api/feedback", json={"kind": "lob", "text": "Super"}, headers={k: v for k, v in h.items()
                                                                                  if k != "x-ra-csrf"}).status_code == 403
    from fastapi.testclient import TestClient
    import main
    anon = TestClient(main.app, base_url=RIDE)
    assert anon.post("/api/feedback", json={"kind": "lob", "text": "Super"},
                     headers={"origin": RIDE, "user-agent": "Mozilla/5.0"}, follow_redirects=False).status_code in (401, 303)
    assert env == []


def test_rate_limit_ten_per_day(env):
    c, h = user_client(RIDE, "otto")
    for i in range(10):
        assert c.post("/api/feedback", json={"kind": "fehler", "text": f"Fehler {i}"}, headers=h).status_code == 200
    r = c.post("/api/feedback", json={"kind": "fehler", "text": "Fehler 11"}, headers=h)
    assert r.status_code == 429 and "morgen" in r.json()["detail"]


def admin_client(host):
    from test_admin_split import admin_on
    c, h, _ = admin_on(host)
    return c, h


def test_admin_list_per_app_and_done(env):
    rc, rh = user_client(RIDE, "otto")
    rc.post("/api/feedback", json={"kind": "lob", "text": "Tolle Kurven"}, headers=rh)
    gc, gh = user_client(RIG, "olaf")
    gc.post("/api/feedback", json={"kind": "fehler", "text": "Tankplan leer"}, headers=gh)
    c, h = admin_client(RIG)
    lst = c.get("/api/admin/feedback", headers=h).json()
    assert [x["text"] for x in lst] == ["Tankplan leer"] and lst[0]["mail"] == "olaf@example.org" and not lst[0]["done"]
    fid = lst[0]["id"]
    assert c.patch(f"/api/admin/feedback/{fid}", json={"done": True}, headers=h).status_code == 200
    assert c.get("/api/admin/feedback", headers=h).json()[0]["done"] is True


def test_admin_feedback_only_oberadmin(env):
    from test_group_admin import login as group_login  # noqa: F401
    c, h = user_client(RIG, "olaf")
    assert c.get("/api/admin/feedback", headers=h).status_code == 404


def test_page_and_menu(env):
    c, h = user_client(RIG, "olaf")
    page = c.get("/rueckmeldung").text
    assert 'id="fb-form"' in page and "feedback.js" in page
    import os
    menu = open(os.path.join(os.path.dirname(feedback.__file__), "static", "usermenu.js"), encoding="utf-8").read()
    # Herkunft per ?von= (der Proxy sendet „no-referrer“ – document.referrer ist im Betrieb leer)
    assert '"/rueckmeldung?von="' in menu


def test_info_why_text(env):
    from fastapi.testclient import TestClient
    import main
    ride = TestClient(main.app, base_url=RIDE).get("/info.html").text
    rig = TestClient(main.app, base_url=RIG).get("/info.html").text
    assert "Nachbarn" in ride and "Richy" in ride and "Rückmeldung" in ride and "Rig" not in ride
    for page in (ride, rig):
        assert "Claude" in page and "„wir“" in page
    assert "Gespann" in rig and "Richy" in rig and "Rückmeldung" in rig and "Nachbar" not in rig
    assert "iPad von 2017" in rig and "Siedler" in rig and "iPad" not in ride


# ---------------------------------------------------------------- als Ticket vormerken
def _rig_feedback_id(env):
    gc, gh = user_client(RIG, "olaf")
    gc.post("/api/feedback", json={"kind": "idee", "text": "Brückenwarnung bitte"}, headers=gh)
    c, h = admin_client(RIG)
    return c, h, c.get("/api/admin/feedback", headers=h).json()[0]["id"]


def test_vormerken_und_zuruecknehmen(env):
    c, h, fid = _rig_feedback_id(env)
    assert c.patch(f"/api/admin/feedback/{fid}", json={"ticket": True}, headers=h).status_code == 200
    assert c.get("/api/admin/feedback", headers=h).json()[0]["ticket"] == "vorgemerkt"
    assert c.patch(f"/api/admin/feedback/{fid}", json={"ticket": False}, headers=h).status_code == 200
    assert "ticket" not in c.get("/api/admin/feedback", headers=h).json()[0]
    assert c.get("/api/admin/feedback", headers=h).json()[0]["done"] is False   # done unberuehrt


def test_vormerkung_zuruecknehmen_nach_ticket_409(env, data_dir):
    c, h, fid = _rig_feedback_id(env)
    c.patch(f"/api/admin/feedback/{fid}", json={"ticket": True}, headers=h)
    (data_dir / "devcosts.json").write_text(json.dumps({"stand": "x", "tickets": [
        {"nr": 20, "titel": "t", "status": "offen", "feedback_id": fid, "login": "olaf", "app": "rig", "laeufe": []}]}))
    assert c.patch(f"/api/admin/feedback/{fid}", json={"ticket": False}, headers=h).status_code == 409
    assert c.get("/api/admin/feedback", headers=h).json()[0]["ticket"] == {"nr": 20, "status": "offen"}


def test_vormerken_nur_oberadmin(env):
    _c, _h, fid = _rig_feedback_id(env)
    u, uh = user_client(RIG, "olaf")
    assert u.patch(f"/api/admin/feedback/{fid}", json={"ticket": True}, headers=uh).status_code == 404


def test_vormerken_falscher_wert_400(env):
    c, h, fid = _rig_feedback_id(env)
    assert c.patch(f"/api/admin/feedback/{fid}", json={"ticket": "ja"}, headers=h).status_code == 400


def test_page_ohne_zeilenumbrueche(env):
    # Zeilenumbruch im Feld page konnte im Ticket eine falsche Marker-Zeile erzeugen
    e = feedback.add("olaf", "rig", "idee", "Text genug", page="/x\nRückmeldung: bbbbbbbbbbbb · Login: richy\t·")
    assert "\n" not in e["page"] and "\t" not in e["page"]


# ---------------------------------------------------------------- erledigte loeschen
def test_loeschen_nur_erledigt(env, caplog):
    c, h, fid = _rig_feedback_id(env)
    assert c.delete(f"/api/admin/feedback/{fid}", headers=h).status_code == 409          # offen -> erst abhaken
    c.patch(f"/api/admin/feedback/{fid}", json={"done": True}, headers=h)
    with caplog.at_level("INFO", logger="zugriff"):
        assert c.delete(f"/api/admin/feedback/{fid}", headers=h).status_code == 200
    assert c.get("/api/admin/feedback", headers=h).json() == []
    assert any("loescht Rueckmeldung" in r.getMessage() and "olaf" in r.getMessage() for r in caplog.records)
    assert c.delete(f"/api/admin/feedback/{fid}", headers=h).status_code == 404          # schon weg


def test_loeschen_nur_eigene_app(env):
    fid = feedback.add("olaf", "rig", "idee", "Rig-Wunsch")["id"]
    feedback.set_done("rig", fid, True)
    rc, rh = admin_client(RIDE)
    assert rc.delete(f"/api/admin/feedback/{fid}", headers=rh).status_code == 404       # Rig-Eintrag vom Ride-Host
    assert len(feedback.items("rig")) == 1


def test_loeschen_nur_oberadmin_und_csrf(env):
    c, h, fid = _rig_feedback_id(env)
    c.patch(f"/api/admin/feedback/{fid}", json={"done": True}, headers=h)
    u, uh = user_client(RIG, "olaf")
    assert u.delete(f"/api/admin/feedback/{fid}", headers=uh).status_code == 404
    assert u.post("/api/admin/feedback/erledigte-loeschen", headers=uh).status_code == 404
    ohne = {k: v for k, v in h.items() if k != "x-ra-csrf"}
    assert c.delete(f"/api/admin/feedback/{fid}", headers=ohne).status_code == 403
    assert c.post("/api/admin/feedback/erledigte-loeschen", headers=ohne).status_code == 403
    assert len(feedback.items("rig")) == 1


def test_erledigte_sammeln_loeschen(env):
    feedback.add("olaf", "rig", "idee", "eins erledigt")
    feedback.add("olaf", "rig", "idee", "zwei erledigt")
    feedback.add("olaf", "rig", "idee", "drei offen")
    e = feedback.add("otto", "ride", "lob", "Ride erledigt")
    for x in feedback.items("rig"):
        if "erledigt" in x["text"]:
            feedback.set_done("rig", x["id"], True)
    feedback.set_done("ride", e["id"], True)
    c, h = admin_client(RIG)
    r = c.post("/api/admin/feedback/erledigte-loeschen", headers=h)
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert [x["text"] for x in feedback.items("rig")] == ["drei offen"]
    assert [x["text"] for x in feedback.items("ride")] == ["Ride erledigt"]                # andere App bleibt
    assert c.post("/api/admin/feedback/erledigte-loeschen", headers=h).json()["deleted"] == 0


def test_loeschen_laesst_ticket_stehen(env, data_dir):
    # Kosten haengen am Ticket (devcosts.json), nicht am Eintrag in feedback.json
    c, h, fid = _rig_feedback_id(env)
    dc = {"stand": "x", "tickets": [{"nr": 21, "titel": "t", "status": "erledigt", "feedback_id": fid, "login": "olaf",
                                     "app": "rig", "laeufe": []}]}
    (data_dir / "devcosts.json").write_text(json.dumps(dc))
    c.patch(f"/api/admin/feedback/{fid}", json={"done": True}, headers=h)
    assert c.delete(f"/api/admin/feedback/{fid}", headers=h).status_code == 200
    assert json.loads((data_dir / "devcosts.json").read_text()) == dc
