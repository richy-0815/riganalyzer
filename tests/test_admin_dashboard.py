"""0.37.2: Admin-Bereich als Dashboard – Kacheln in zwei Spalten, verschiebbar, halb/ganz breit, einklappbar.

Der Server rendert je Rolle nur die Kacheln, die der Admin sieht (Gruppen-Admin ohne Gruppen/Kosten/Rückmeldungen),
mit Standard-Breite/-Spalte als data-Attribute; die Anordnung liegt am Login unter prefs „admin“.
"""
import os
import re

import pytest

from test_admin_costs import RIDE, RIG, admin_on, env, login_as  # noqa: F401 – env: Fixture (Logins bofh/mia)

STD = ["logins", "aktiv", "rueckmeldungen", "gruppen", "kosten", "newsletter", "aenderungen", "versionen"]


def cards(page):
    """(id, wide, col) je Kachel in Seitenreihenfolge."""
    out = []
    for tag in re.findall(r"<section[^>]*data-acard=[^>]*>", page):
        cid = re.search(r'data-acard="([a-z]+)"', tag).group(1)
        assert re.search(r'data-title="[^"]+"', tag), tag
        out.append((cid, 'data-wide="1"' in tag, (re.search(r'data-col="([lr])"', tag) or [None, None])[1]))
    return out


@pytest.mark.usefixtures("env")
def test_oberadmin_rig_sieht_alle_kacheln_im_standard_b():
    c, h, _ = admin_on(RIG)
    page = c.get("/admin", headers=h).text
    got = cards(page)
    assert [x[0] for x in got] == STD
    assert {x[0] for x in got if x[1]} == {"logins", "newsletter", "aenderungen", "versionen"}   # ganz breit
    assert {x[0]: x[2] for x in got if not x[1]} == {"aktiv": "l", "rueckmeldungen": "r", "gruppen": "l", "kosten": "r"}
    assert 'id="adm-cards"' in page and 'id="adm-reset"' in page
    assert page.index("cards.js") < page.index("admin-cards.js")   # gemeinsame Logik vor der Seiten-Initialisierung


@pytest.mark.usefixtures("env")
def test_oberadmin_ride_ohne_kosten():
    _, _, sec = admin_on(RIG)
    c, h, _ = admin_on(RIDE, sec)
    assert [x[0] for x in cards(c.get("/admin", headers=h).text)] == [x for x in STD if x != "kosten"]


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize("host", [RIG, RIDE])
def test_gruppen_admin_nur_seine_kacheln(host):
    m, mh = login_as(host, "mia")
    page = m.get("/admin", headers=mh).text
    assert [x[0] for x in cards(page)] == ["logins", "aktiv", "aenderungen", "versionen"], host
    assert {x[0] for x in cards(page) if x[1]} == {"logins", "aktiv", "aenderungen", "versionen"}, host   # aktiv breit (keine rechte Spalte)
    for gone in ('id="grp-list"', 'id="grp-new"', 'id="cost-card"', 'id="fb-card"', "<!--ober-->"):
        assert gone not in page, (host, gone)


def test_admin_js_verkraftet_fehlende_gruppen_kachel():
    """Beim Gruppen-Admin fehlt #grp-new im HTML – admin.js darf daran nicht abbrechen."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "admin.js"), encoding="utf-8").read()
    assert '$("grp-new").addEventListener' not in src


def test_prefs_admin_anordnung_gueltig():
    import prefs
    prefs._check("admin", {"order": STD, "collapsed": ["kosten"], "wide": ["logins", "aktiv"],
                           "col": {"gruppen": "r", "kosten": "l"}, "at": 1})
    prefs._check("admin", {"order": [], "collapsed": [], "at": 1})


@pytest.mark.parametrize("bad", [
    {"order": ["boese"], "at": 1},
    {"order": "logins", "at": 1},
    {"order": ["logins", "logins"], "at": 1},
    {"order": STD * 2, "at": 1},
    {"wide": ["x"], "at": 1},
    {"collapsed": [1], "at": 1},
    {"col": {"logins": "m"}, "at": 1},
    {"col": {"boese": "l"}, "at": 1},
    {"col": ["l"], "at": 1},
    {"order": STD, "extra": 1, "at": 1},
    {"order": STD},
])
def test_prefs_admin_anordnung_streng(bad):
    import prefs
    with pytest.raises(prefs.PrefsError):
        prefs._check("admin", bad)


@pytest.mark.usefixtures("env")
def test_prefs_admin_ueber_api():
    c, h, _ = admin_on(RIG)
    lay = {"order": STD[::-1], "collapsed": ["aktiv"], "wide": ["aktiv"], "col": {"gruppen": "r"}, "at": 5}
    r = c.put("/api/me/prefs", json={"admin": lay}, headers=h)
    assert r.status_code == 200, r.text
    assert c.get("/api/me/prefs", headers=h).json()["admin"] == lay
    assert c.put("/api/me/prefs", json={"admin": {**lay, "col": {"x": "l"}, "at": 6}}, headers=h).status_code == 422
