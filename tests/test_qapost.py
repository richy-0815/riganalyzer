"""QA-Postfach (0.40.1, Ticket #10): Ablage nur mit STAGE=qa + MAIL_OFF=1, Seite /qa/postfach nur in QA fuer Admins mit 2FA."""
import json

import mailer
import qapost
from test_group_admin import HOST, ORIGIN, env, login  # noqa: F401  (Fixture env)


def _qa(monkeypatch, stage="qa"):
    monkeypatch.setenv("STAGE", stage)
    monkeypatch.setenv("MAIL_OFF", "1")


def test_prod_legt_nie_etwas_ab(data_dir, monkeypatch):
    _qa(monkeypatch, "prod")
    assert mailer.send("a@example.org", "Betreff", "Text https://x/y?t=geheim") is False
    assert not (data_dir / "qa-postfach.json").exists()
    monkeypatch.delenv("STAGE")
    assert qapost.store("a@example.org", "B", "T") is False


def test_qa_ohne_mail_off_legt_nichts_ab(data_dir, monkeypatch):
    monkeypatch.setenv("STAGE", "qa")
    monkeypatch.delenv("MAIL_OFF", raising=False)
    assert qapost.store("a@example.org", "B", "T") is False
    assert not (data_dir / "qa-postfach.json").exists()


def test_qa_legt_ab_und_behaelt_nur_die_letzten(data_dir, monkeypatch):
    _qa(monkeypatch)
    assert mailer.send("neu@example.org", "Neue Mailadresse", "Link: https://qa.example/mail-bestaetigen?t=abc", "rig") is False
    for i in range(qapost.MAX + 5):
        qapost.store(f"u{i}@example.org", f"Mail {i}", "x")
    mails = qapost.items()
    assert len(mails) == qapost.MAX
    assert mails[0]["subject"] == "Mail 5" and mails[-1]["subject"] == f"Mail {qapost.MAX + 4}"
    assert oct((data_dir / "qa-postfach.json").stat().st_mode & 0o777) == "0o600"
    assert qapost.clear() is True and qapost.items() == [] and qapost.clear() is False


def test_seite_escaped_und_links_klickbar():
    html = qapost.page_body([{"id": "1", "t": 0, "to": "<x>@example.org", "subject": "<b>S</b>", "app": "ride",
                              "body": 'Hallo <script>\nhttps://qa.example/mail-bestaetigen?t=a_b-c&x=1 "ende"'}])
    assert "<script>" not in html and "&lt;b&gt;S&lt;/b&gt;" in html
    assert '<a href="https://qa.example/mail-bestaetigen?t=a_b-c&amp;x=1" rel="noopener">' in html
    assert 'href="javascript' not in qapost.page_body([{"body": "javascript:alert(1)"}])   # nur http(s) wird zum Link


def test_route_prod_404(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    import main
    monkeypatch.setattr(main, "STAGE", "prod")
    c, _ = login()
    assert c.get("/qa/postfach", headers=ORIGIN).status_code == 404


def test_route_qa_nur_admin_mit_2fa(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    import main
    from test_admin_0141 import plain_login
    monkeypatch.setattr(main, "STAGE", "qa")
    _qa(monkeypatch)
    qapost.store("neu@example.org", "Neue Mailadresse für den RideAnalyzer", f"{HOST}/mail-bestaetigen?t=tok123")
    # Nicht-Admin: 404 (keine Auskunft)
    assert plain_login("otto").get("/qa/postfach", headers=ORIGIN).status_code == 404
    # Admin ohne 2FA: zur Einrichtung umgeleitet, nie die Mails
    r = plain_login("gina").get("/qa/postfach", headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and "tok123" not in r.text
    # Admin mit 2FA: sieht die Mail samt Link
    c, _ = login()
    r = c.get("/qa/postfach", headers=ORIGIN)
    assert r.status_code == 200 and "tok123" in r.text and "Neue Mailadresse" in r.text


def test_seed_leert_postfach(data_dir, monkeypatch):
    _qa(monkeypatch)
    qapost.store("a@example.org", "B", "T")
    assert json.loads((data_dir / "qa-postfach.json").read_text())["mails"]
    assert qapost.clear() and not (data_dir / "qa-postfach.json").exists()
