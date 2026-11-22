"""0.36.0: „Meine Daten“ im Konto (DSGVO-Auskunft) + Passwort/Mail eingeklappt."""
import json

import pytest

import auth

RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"
SECRET_KEYS = ("hash", "totp_secret", "totp_backup", "totp_pending", "totp_last")


@pytest.fixture
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    h = auth.hash_pw("geheim12345")
    users = {"olaf": {"hash": h, "apps": ["ride", "rig"], "mail": "olaf@example.org", "registered": "2026-10-01",
                     "totp_secret": "GEHEIMTOTP", "totp_backup": ["bk1"], "settings": {"speed_warn": False}},
             "otto": {"hash": h, "apps": ["rig"]}}
    (data_dir / "users.json").write_text(json.dumps({"users": users}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    import activity
    import reviews
    monkeypatch.setattr(reviews, "FILE", str(data_dir / "reviews.sqlite"))
    activity.reset_for_tests()
    import main

    def make(host, user="olaf"):
        c = TestClient(main.app, base_url=host)
        o = {"origin": host, "user-agent": "Mozilla/5.0"}
        c.post("/login", data={"user": user, "password": "geheim12345"}, headers=o, follow_redirects=False)
        return c, o
    return make


def _trip(c, o, name="Testtour"):
    body = {"name": name, "stops": [{"lat": 52.5, "lon": 13.4, "name": "Berlin"}, {"lat": 48.1, "lon": 11.6, "name": "München"}]}
    csrf = c.cookies.get("ra_csrf")
    r = c.post("/api/trips", json=body, headers={**o, "X-RA-CSRF": csrf})
    assert r.status_code == 200, r.text
    return r.json()


def test_download_vollstaendig_ohne_geheimnisse(client):
    c, o = client(RIG)
    _trip(c, o)
    r = c.get("/api/me/data", headers=o)
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"] and "meine-daten-olaf" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    d = r.json()
    assert d["login"] == "olaf"
    assert d["konto"]["mail"] == "olaf@example.org" and d["konto"]["settings"] == {"speed_warn": False}
    assert [t["trip"]["name"] for t in d["touren"]] == ["Testtour"]
    assert "navi_paket" in d["touren"][0] and "fruehere_staende" in d["touren"][0]
    assert d["anmeldungen"] and d["anmeldungen"][0]["ok"] is True
    for k in ("vorlieben", "platz_bewertungen", "rueckmeldungen", "verbrauch"):
        assert k in d
    text = r.text
    for k in SECRET_KEYS:
        assert f'"{k}"' not in text
    assert "GEHEIMTOTP" not in text and "bk1" not in text and "$" not in json.dumps(d["konto"])


def test_download_nur_eigene_daten(client):
    c, o = client(RIG)
    _trip(c, o, "Uwes Tour")
    c2, o2 = client(RIG, "otto")
    d = c2.get("/api/me/data", headers=o2).json()
    assert d["login"] == "otto" and d["touren"] == [] and "Uwes Tour" not in json.dumps(d)


def test_download_ohne_anmeldung_gesperrt(client):
    from fastapi.testclient import TestClient
    import main
    r = TestClient(main.app, base_url=RIG).get("/api/me/data", headers={"user-agent": "Mozilla/5.0"}, follow_redirects=False)
    assert r.status_code in (401, 303)
    r = TestClient(main.app, base_url=RIG).get("/api/me/data/overview", headers={"user-agent": "Mozilla/5.0"}, follow_redirects=False)
    assert r.status_code in (401, 303)


def test_uebersicht_rig_mit_touren(client):
    import reviews
    reviews.save("olaf", {"place": "osm:n1", "name": "Platz am See", "lat": 45.1, "lon": 6.2, "stars": 4})
    c, o = client(RIG)
    _trip(c, o)
    assert [x["name"] for x in c.get("/api/me/data", headers=o).json()["platz_bewertungen"]] == ["Platz am See"]
    v = c.get("/api/me/data/overview", headers=o).json()
    assert dict((x["label"], x["value"]) for x in v["gespeichert"])["Platz-Bewertungen"] == "1"
    labels = [x["label"] for x in v["gespeichert"]]
    assert "Touren" in labels and "Navi-Pakete" in labels and "Platz-Bewertungen" in labels
    assert dict((x["label"], x["value"]) for x in v["gespeichert"])["Touren"].startswith("1 Tour")
    assert v["anmeldungen"] and set(v["anmeldungen"][0]) == {"t", "ip", "device", "ok"}
    assert v["monat"]["werte"]


def test_uebersicht_ride_ohne_rig_begriffe(client):
    c, o = client(RIG)
    _trip(c, o)
    c, o = client(RIDE)
    v = c.get("/api/me/data/overview", headers=o).json()
    blob = json.dumps(v, ensure_ascii=False)
    for w in ("Tour", "Navi", "RigNavi", "Platz-Bewertung", "Paket"):
        assert w not in blob, w


def test_konto_seite_hat_meine_daten_und_eingeklappt(client):
    for host in (RIDE, RIG):
        c, o = client(host)
        page = c.get("/konto", headers=o).text
        assert 'id="kt-daten"' in page and "konto-daten.js" in page and 'href="/api/me/data"' in page
        assert '<details class="kt-fold" id="kt-pw">' in page and '<details class="kt-fold" id="kt-mailbox">' in page
        assert "<details class=\"kt-fold\" id=\"kt-pw\" open" not in page


def test_datenschutz_verweist_auf_meine_daten():
    import os
    app = os.path.join(os.path.dirname(__file__), "..", "app")
    # Datenschutz steht in den Häufigen Fragen – „So geht's“ verlinkt dorthin, die FAQ nennt „Meine Daten“
    for f in ("static-ride/index.html", "static-rig/index.html"):
        assert 'href="/faq.html#datenschutz-was-wird-gespeichert"' in open(os.path.join(app, f), encoding="utf-8").read(), f
    faq = os.path.join(app, "..", "faq", "06-datenschutz.md")
    assert "Meine Daten" in open(faq, encoding="utf-8").read()


# ---------------------------------------------------------------- Kacheln verschieben
def test_prefs_konto_anordnung():
    import prefs
    prefs._check("konto", {"order": ["daten", "zugang", "touren", "rignavi"], "collapsed": ["daten"], "at": 1})
    for bad in ({"order": ["boese"], "at": 1}, {"order": "daten", "at": 1}, {"collapsed": ["x"] * 2, "at": 1},
                {"order": ["daten"] * 20, "at": 1}):
        with pytest.raises(prefs.PrefsError):
            prefs._check("konto", bad)


def test_konto_kacheln_markiert(client):
    import re
    for host, ids in ((RIG, ["touren", "zugang", "mail", "daten", "rignavi"]), (RIDE, ["zugang", "mail", "daten"])):
        c, o = client(host)
        page = c.get("/konto", headers=o).text
        assert re.findall(r'data-kcard="([a-z]+)"', page) == ids
        assert "konto-cards.js" in page and 'id="kt-cards"' in page and 'id="kt-reset"' in page


def test_kachel_leiste_kollidiert_nicht_mit_tour_knoepfen():
    """0.36.0: Die Kachel-Leiste hiess erst .kt-tools wie die Knopfzeile der Touren – deren Knoepfe waren unsichtbar."""
    import os
    import re
    st = os.path.join(os.path.dirname(__file__), "..", "app", "static")
    touren = set(re.findall(r'className: "([a-z-]+)', open(os.path.join(st + "-rig", "konto-touren.js"), encoding="utf-8").read()))
    cards = set()
    for f in ("konto-cards.js", "cards.js"):   # steckt die Kachel-Logik in cards.js
        src = open(os.path.join(st, f), encoding="utf-8").read()
        cards |= set(re.findall(r'\.(kt-[a-z-]+)', src)) | set(re.findall(r'className = "([a-z-]+)', src))
        cards |= set(re.findall(r'wrap\("([a-z-]+)"', src))
    assert touren and cards and not (touren & cards), touren & cards


# ---------------------------------------------------------------- Mailwechsel mit Bestätigung
def test_export_zeigt_offenen_mailwechsel_ohne_token(client, monkeypatch):
    import mailchange
    import mailer
    monkeypatch.setattr(mailer, "_smtp_send", lambda msg: None)
    c, o = client(RIG)
    mailchange.request("olaf", "neu@example.org", RIG, "rig")
    w = c.get("/api/me/data", headers=o).json()["konto"]["mail_wechsel"]
    assert w["mail"] == "neu@example.org" and w["gueltig_bis"].startswith("20")
    assert set(w) == {"mail", "gueltig_bis"}
    konto = next(x for x in c.get("/api/me/data/overview", headers=o).json()["gespeichert"] if x["label"] == "Konto")
    assert "wartet auf Bestätigung" in konto["value"]


def test_export_ohne_mailwechsel_hat_keinen_eintrag(client):
    c, o = client(RIG)
    assert "mail_wechsel" not in c.get("/api/me/data", headers=o).json()["konto"]


def test_konto_seite_mailwechsel_felder(client):
    c, o = client(RIDE)
    page = c.get("/konto", headers=o).text
    for needle in ('id="kt-mail-pw"', 'autocomplete="current-password"', "Bestätigungslink schicken",
                   'id="kt-mail-pending"', 'id="kt-mail-again"', 'id="kt-mail-cancel"'):
        assert needle in page, needle
    assert "Mail speichern" not in page


def test_uebersicht_rig_fahrzeugprofile(client):
    # Fahrzeugprofile eigene Zeile mit Namen (stehen am Login, auch im Download unter „vorlieben“)
    import prefs
    c, o = client(RIG)
    v = dict((x["label"], x["value"]) for x in c.get("/api/me/data/overview", headers=o).json()["gespeichert"])
    assert v["Fahrzeugprofile"] == "0 Profile"
    prefs.put("olaf", {"profiles": {"active": "Womo", "profiles": {"Womo": {"height": 3.1}, "Gespann": {"height": 2.9}},
                                    "at": 1}})
    v = dict((x["label"], x["value"]) for x in c.get("/api/me/data/overview", headers=o).json()["gespeichert"])
    assert v["Fahrzeugprofile"] == "2 Profile (Womo, Gespann)"
    assert c.get("/api/me/data", headers=o).json()["vorlieben"]["profiles"]["profiles"]["Womo"]["height"] == 3.1
    c, o = client(RIDE)
    assert "Fahrzeugprofile" not in [x["label"] for x in c.get("/api/me/data/overview", headers=o).json()["gespeichert"]]


def test_meine_daten_geteilte_touren(client):
    # Einladungen gesendet/erhalten – im Download nur Metadaten, nie der Stand
    import shares
    import trips
    t = trips.save("olaf", {"name": "Lausitz", "stops": [{"lat": 52.5, "lon": 13.4, "name": "A"}, {"lat": 51.7, "lon": 14.3, "name": "B"}]})
    shares.invite("olaf", "otto", t["id"])
    c, o = client(RIG, "otto")
    got = c.get("/api/me/data", headers=o).json()["geteilte_touren"]
    assert len(got["erhalten"]) == 1 and got["gesendet"] == [] and "snapshot" not in got["erhalten"][0]
    c, o = client(RIG)
    v = dict((x["label"], x["value"]) for x in c.get("/api/me/data/overview", headers=o).json()["gespeichert"])
    assert v["Geteilte Touren"] == "1 gesendet, 0 erhalten"
    c, o = client(RIDE)
    assert "Geteilte Touren" not in [x["label"] for x in c.get("/api/me/data/overview", headers=o).json()["gespeichert"]]
