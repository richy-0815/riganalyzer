"""0.39.0: Rueckmeldung -> Ticket -> Kosten je Login (devcosts.json vom Rueckmeldungs-Abgleich, App liest nur)."""
import json

import pytest

import devcosts

LAUF = {"modell": "claude-sonnet-5-5"}


def ticket(nr, login, app, laeufe, status="offen", fid=None):
    return {"nr": nr, "titel": f"Idee von {login}: {nr}", "status": status, "feedback_id": fid or f"f{nr:011d}",
            "login": login, "app": app, "laeufe": [{**LAUF, "zeit": z, "usd": u} for z, u in laeufe]}


@pytest.fixture
def dc(data_dir):
    def schreibe(tickets):
        (data_dir / "devcosts.json").write_text(json.dumps({"stand": "2026-11-09T12:00:00", "tickets": tickets}))
    return schreibe


def test_devcosts_monat_summen_und_euro(dc):
    dc([ticket(20, "olaf", "rig", [("2026-11-08T15:00:00", 0.10), ("2026-11-09T09:00:00", 0.30), ("2026-10-31T23:00:00", 5.0)]),
        ticket(21, "olaf", "rig", [("2026-11-02T10:00:00", 0.60)], status="erledigt"),
        ticket(22, "otto", "ride", [("2026-11-05T10:00:00", 2.00)])])
    m = devcosts.monat("2026-11", 0.5)
    assert m["stand"] == "2026-11-09T12:00:00"
    assert [x["login"] for x in m["logins"]] == ["otto", "olaf"]   # nach Euro absteigend (otto 1,00 €, olaf 0,50 €)
    olaf = next(x for x in m["logins"] if x["login"] == "olaf")
    assert olaf["usd"] == pytest.approx(1.0)
    assert olaf["eur"] == pytest.approx(0.5)
    assert [(t["nr"], t["status"], t["usd"]) for t in olaf["tickets"]] == [(20, "offen", pytest.approx(0.4)),
                                                                        (21, "erledigt", pytest.approx(0.6))]


def test_devcosts_nach_euro_sortiert(dc):
    dc([ticket(1, "a", "rig", [("2026-11-01T00:00:00", 0.1)]), ticket(2, "b", "ride", [("2026-11-01T00:00:00", 0.9)])])
    assert [x["login"] for x in devcosts.monat("2026-11", 1)["logins"]] == ["b", "a"]


def test_devcosts_beide_apps_im_monat(dc):
    dc([ticket(1, "olaf", "rig", [("2026-11-01T00:00:00", 0.1)]), ticket(2, "otto", "ride", [("2026-11-01T00:00:00", 0.2)])])
    apps = {t["app"] for x in devcosts.monat("2026-11", 1)["logins"] for t in x["tickets"]}
    assert apps == {"ride", "rig"}


def test_devcosts_ohne_laeufe_im_monat_nicht_gelistet(dc):
    dc([ticket(1, "olaf", "rig", [("2026-10-01T00:00:00", 0.1)]), ticket(2, "otto", "ride", [])])
    assert devcosts.monat("2026-11", 1)["logins"] == []


def test_devcosts_kaputt_none(data_dir):
    assert devcosts.load() is None
    (data_dir / "devcosts.json").write_text("{kaputt")
    assert devcosts.load() is None
    (data_dir / "devcosts.json").write_text(json.dumps({"tickets": "falsch"}))
    assert devcosts.load() is None
    assert devcosts.monat("2026-11", 1) is None


def test_ticket_fuer(dc):
    dc([ticket(20, "olaf", "rig", [], fid="a1b2c3d4e5f6", status="erledigt")])
    assert devcosts.ticket_fuer("a1b2c3d4e5f6") == {"nr": 20, "status": "erledigt"}
    assert devcosts.ticket_fuer("gibtsnicht") is None


def test_devcosts_kaputte_eintraege_kein_absturz(data_dir):
    (data_dir / "devcosts.json").write_text(json.dumps({"stand": "x", "tickets": [
        {"nr": "7", "login": "a", "app": "rig", "laeufe": [{"zeit": "2026-11-01T00:00:00", "usd": 0.1}]},
        {"nr": 3, "login": "a", "app": "rig", "laeufe": [{"zeit": "2026-11-01T00:00:00", "usd": 0.2}]}]}))
    assert [t["nr"] for t in devcosts.monat("2026-11", 1)["logins"][0]["tickets"]] == [3, "7"]
    (data_dir / "devcosts.json").write_text(json.dumps({"stand": "x", "tickets": [{"nr": 1, "laeufe": [1]}]}))
    assert devcosts.monat("2026-11", 1) is None
