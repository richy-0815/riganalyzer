"""Waehlbare Mails (0.50.0, Ticket #16): Einstellungen, Abmeldelink, abendliche Sammelmail, Newsletter."""
import datetime as dt
import json

import pytest

import auth
import mailer
import notify
import prefs
from test_admin import ORIGIN, env, login  # noqa: F401  (Fixture env)


@pytest.fixture
def users(data_dir, monkeypatch):
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "anna": {"hash": "x", "apps": ["rig"], "mail": "anna@example.org"},
        "bert": {"hash": "x", "apps": ["ride", "rig"], "mail": "bert@example.org"},
        "carl": {"hash": "x", "apps": ["rig"]},                                   # ohne Mail
        "dora": {"hash": "x", "apps": ["rig"], "mail": "dora@example.org", "locked": True},
        "emil": {"hash": "x", "apps": ["ride"], "mail": "emil@example.org"}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    return data_dir


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    def fake(to, subject, body, app=None, sender=None, headers=None, html=None):
        sent.append({"to": to, "subject": subject, "body": body, "app": app, "sender": sender, "headers": headers or {}, "html": html})
        return True
    monkeypatch.setattr(mailer, "send", fake)
    return sent


def _on(user, **kw):
    prefs.put(user, {"mail": {**kw, "at": 1_700_000_000_000}})


def test_defaults_off_and_prefs_validation(users):
    assert notify.settings("anna") == {"releases": False, "newsletter": False, "asked": False}
    _on("anna", releases=True, asked=True)
    assert notify.settings("anna") == {"releases": True, "newsletter": False, "asked": True}
    with pytest.raises(prefs.PrefsError):
        prefs.put("anna", {"mail": {"releases": "ja", "at": 1_800_000_000_000}})
    with pytest.raises(prefs.PrefsError):
        prefs.put("anna", {"mail": {"spam": True, "at": 1_800_000_000_000}})


def test_recipients_only_this_app_with_mail_not_locked(users):
    for u in ("anna", "bert", "carl", "dora", "emil"):
        _on(u, releases=True, newsletter=True)
    assert notify.recipients("rig", "releases") == [("anna", "anna@example.org"), ("bert", "bert@example.org")]
    assert notify.recipients("ride", "newsletter") == [("bert", "bert@example.org"), ("emil", "emil@example.org")]
    _on("bert", releases=False, newsletter=True)   # aelterer Zeitstempel verliert nicht – gleicher at: neuer gewinnt
    assert [u for u, _ in notify.recipients("rig", "releases")] == ["anna"]


def test_token_roundtrip_and_forgery(users):
    t = notify.token("anna", "newsletter", "rig")
    assert notify.check(t, "rig") == ("anna", "newsletter")
    with pytest.raises(notify.Invalid):
        notify.check(t, "ride")                                   # Link aus der Rig-Mail gilt nicht in Ride
    with pytest.raises(notify.Invalid):
        notify.check(t[:-1] + ("0" if t[-1] != "0" else "1"), "rig")
    with pytest.raises(notify.Invalid):
        notify.check("kaputt", "rig")
    fake = notify._b64("bert|newsletter|rig") + "." + t.split(".")[1]
    with pytest.raises(notify.Invalid):
        notify.check(fake, "rig")                                 # Signatur von anna passt nicht zu bert


def test_send_has_footer_unsubscribe_headers_and_noreply(users, outbox):
    assert notify.send("anna", "anna@example.org", "releases", "Betreff", "Text", "rig", "https://rig.example")
    m = outbox[0]
    assert m["sender"] == mailer.NEWS_FROM and mailer.NEWS_FROM.startswith("noreply@")
    link = "https://rig.example/abbestellen?t=" + notify.token("anna", "releases", "rig")
    assert link in m["body"] and "„Neue Versionen“" in m["body"]
    assert m["headers"] == {"List-Unsubscribe": f"<{link}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def test_mailer_sets_sender_and_headers(monkeypatch):
    got = []
    monkeypatch.delenv("MAIL_OFF", raising=False)
    monkeypatch.setattr(mailer, "_smtp_send", got.append)
    assert mailer.send("a@example.org", "S", "B", "rig", sender="noreply@example.org", headers={"List-Unsubscribe": "<https://x/y>"})
    assert "noreply@example.org" in got[0]["From"] and "RigAnalyzer" in got[0]["From"]
    assert got[0]["List-Unsubscribe"] == "<https://x/y>"


REL = [
    {"version": "0.50.1", "date": "2026-11-18", "app": "beide", "public": True, "public_text": "Allgemein",
     "public_text_rig": "Für Rig", "public_text_ride": "Kleinere Verbesserungen im Hintergrund."},
    {"version": "0.50.0", "date": "2026-11-18", "app": "rig", "public": True, "public_text": "Nur Rig"},
    {"version": "0.49.1", "date": "2026-11-17", "app": "beide", "public": False, "public_text": "intern"},
    {"version": "0.49.0", "date": "2026-11-17", "app": "beide", "public": True, "public_text": "Alt"},
]


def test_release_items_filter():
    assert notify.release_items(REL, "rig", "0.49.0") == [("0.50.0", "2026-11-18", "Nur Rig"), ("0.50.1", "2026-11-18", "Für Rig")]
    assert notify.release_items(REL, "ride", "0.49.0") == []     # nur Wartung bzw. Rig-only
    assert notify.release_items(REL, "ride", "0.48.9") == [("0.49.0", "2026-11-17", "Alt")]
    subj, body = notify.digest_mail("rig", notify.release_items(REL, "rig", "0.49.0"))
    assert subj == "RigAnalyzer: 2 Neuerungen" and "• 0.50.0 (18.11.): Nur Rig" in body


def test_digest_first_run_only_remembers(users, outbox):
    _on("anna", releases=True)
    assert notify.digest_tick("rig", dt.datetime(2026, 11, 18, 20), REL, "0.49.0", "https://r") == 0
    assert outbox == []
    st = json.loads((users / "mail-digest-rig.json").read_text())
    assert st["last"] == "0.49.0"


def test_digest_evening_once_per_day(users, outbox):
    _on("anna", releases=True)
    _on("bert", releases=False)
    notify.digest_tick("rig", dt.datetime(2026, 11, 17, 8), REL, "0.49.0", "https://r")             # merkt Stand
    assert notify.digest_tick("rig", dt.datetime(2026, 11, 18, 18, 59), REL, "0.50.1", "https://r") == 0   # vor 19 Uhr
    assert notify.digest_tick("rig", dt.datetime(2026, 11, 18, 19, 5), REL, "0.50.1", "https://r") == 1
    assert [m["to"] for m in outbox] == ["anna@example.org"]
    assert outbox[0]["subject"] == "RigAnalyzer: 2 Neuerungen" and "/abbestellen?t=" in outbox[0]["body"]
    assert notify.digest_tick("rig", dt.datetime(2026, 11, 18, 22), REL, "0.50.1", "https://r") == 0   # heute schon
    assert notify.digest_tick("rig", dt.datetime(2026, 11, 19, 19), REL, "0.50.1", "https://r") == 0   # nichts Neues
    assert len(outbox) == 1


def test_digest_without_news_sends_nothing(users, outbox):
    _on("emil", releases=True)
    notify.digest_tick("ride", dt.datetime(2026, 11, 17, 8), REL, "0.49.0", "https://r")
    assert notify.digest_tick("ride", dt.datetime(2026, 11, 18, 19), REL, "0.50.1", "https://r") == 0
    assert outbox == []


def test_newsletter_validation_test_and_log(users, outbox):
    _on("anna", newsletter=True)
    _on("bert", newsletter=True)
    with pytest.raises(notify.NewsError):
        notify.newsletter("rig", "x", "Lang genug als Text", "https://r", "bofh")
    r = notify.newsletter("rig", "Hallo", "Das ist der Text.", "https://r", "bofh", test_to=("bofh", "bofh@example.org"))
    assert r == {"sent": 1, "total": 1} and notify.newsletter_log("rig") == []
    r = notify.newsletter("rig", "Hallo", "Das ist der Text.", "https://r", "bofh")
    assert r == {"sent": 2, "total": 2}
    assert notify.newsletter_log("rig")[0]["subject"] == "Hallo"
    assert all(m["sender"] == mailer.NEWS_FROM for m in outbox)


# ---------------------------------------------------------------- Endpunkte
def test_me_reports_notify_and_prefs_put(env):  # noqa: F811
    h = login(env)
    assert env.get("/api/me", headers=h).json()["notify"] == {"releases": False, "newsletter": False, "asked": False}
    r = env.put("/api/me/prefs", json={"mail": {"releases": True, "newsletter": False, "asked": True, "at": 1_800_000_000_000}}, headers=h)
    assert r.status_code == 200
    assert env.get("/api/me", headers=h).json()["notify"]["releases"] is True


def test_konto_has_notify_card(env):  # noqa: F811
    h = login(env)
    html = env.get("/konto", headers=h).text
    assert 'data-kcard="mail"' in html and "konto-notify.js" in html


def test_unsubscribe_without_login(env):  # noqa: F811
    prefs.put("bofh", {"mail": {"releases": True, "newsletter": True, "asked": True, "at": 1_700_000_000_000}})
    t = notify.token("bofh", "newsletter", "rig")
    page = env.get(f"/abbestellen?t={t}", headers={"user-agent": "Mozilla/5.0"})
    assert page.status_code == 200 and "Abbestellen" in page.text
    assert notify.settings("bofh")["newsletter"] is True                 # GET aendert nichts (Mail-Scanner)
    r = env.post("/abbestellen", data={"t": t}, headers={"user-agent": "Mozilla/5.0"})
    assert r.status_code == 200 and "abbestellt" in r.text
    assert notify.settings("bofh") == {"releases": True, "newsletter": False, "asked": True}


def test_unsubscribe_one_click_and_bad_token(env):  # noqa: F811
    prefs.put("bofh", {"mail": {"releases": True, "newsletter": True, "asked": True, "at": 1_700_000_000_000}})
    t = notify.token("bofh", "releases", "rig")
    r = env.post(f"/abbestellen?t={t}", data={"List-Unsubscribe": "One-Click"}, headers={"user-agent": "Mozilla/5.0"})
    assert r.status_code == 200 and notify.settings("bofh")["releases"] is False
    assert env.post("/abbestellen", data={"t": "falsch.x"}, headers={"user-agent": "Mozilla/5.0"}).status_code == 400
    assert "ungültig" in env.get("/abbestellen?t=falsch", headers={"user-agent": "Mozilla/5.0"}).text


def test_admin_newsletter(env, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: sent.append((a, k)) or True)
    assert env.get("/api/admin/newsletter", headers=login(env, "olaf")).status_code == 404   # kein Admin
    env.cookies.clear()
    h = login(env)
    assert env.get("/api/admin/newsletter", headers=h).json()["newsletter"] == 0
    assert env.post("/api/admin/newsletter", json={"subject": "x", "text": "zu kurz"}, headers=h).status_code == 422
    r = env.post("/api/admin/newsletter", json={"subject": "Test", "text": "Hallo zusammen!", "test": True}, headers=h)
    assert r.status_code == 200 and r.json() == {"sent": 1, "total": 1}
    assert sent[0][0][0] == "bofh@example.org"
    assert "Newsletter" in env.get("/admin", headers=h).text


# ---------------------------------------------------------------- Editor (Markdown-light, Vorschau, Entwurf)
def test_render_markdown_light_and_safe():
    h = notify.render("# Titel\nText **fett** *kursiv* <script>x</script>\n\n- eins\n- [Link](https://a.de/?x=1&y=\"2\")\n\n"
                      "Mehr: https://b.de/c. [böse](javascript:alert(1))")
    assert "<h2" in h and "<b>fett</b>" in h and "<i>kursiv</i>" in h
    assert "<script>" not in h and "&lt;script&gt;" in h
    assert "<ul><li>eins</li><li><a href=\"https://a.de/?x=1&amp;y=&quot;2&quot;\"" in h
    assert '<a href="https://b.de/c"' in h                      # Punkt am Satzende gehoert nicht zum Link
    assert "javascript:" not in h.split("böse")[0][-60:] and 'href="javascript' not in h


def test_newsletter_mail_has_html_alternative(users, outbox, monkeypatch):
    got = []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: got.append(k) or True)
    _on("anna", newsletter=True)
    notify.newsletter("rig", "Hallo", "Text mit **fett**.", "https://r", "bofh")
    assert "<b>fett</b>" in got[0]["html"] and "Abbestellen" in got[0]["html"] and "/abbestellen?t=" in got[0]["html"]
    assert got[0]["sender"] == mailer.NEWS_FROM


def test_mailer_multipart(monkeypatch):
    got = []
    monkeypatch.delenv("MAIL_OFF", raising=False)
    monkeypatch.setattr(mailer, "_smtp_send", got.append)
    mailer.send("a@example.org", "S", "Text", "rig", html="<p>Hi</p>")
    parts = [p.get_content_type() for p in got[0].iter_parts()]
    assert parts == ["text/plain", "text/html"]


def test_editor_page_draft_preview(env):  # noqa: F811
    assert env.get("/admin/newsletter", headers=login(env, "olaf"), follow_redirects=False).status_code == 404
    env.cookies.clear()
    h = login(env)
    page = env.get("/admin/newsletter", headers=h)
    assert page.status_code == 200 and "newsletter-editor.js" in page.text and 'id="nl-preview"' in page.text
    assert env.get("/api/admin/newsletter/draft", headers=h).json()["text"] == ""
    assert env.put("/api/admin/newsletter/draft", json={"subject": "S", "text": "# T"}, headers=h).status_code == 200
    assert env.get("/api/admin/newsletter/draft", headers=h).json()["subject"] == "S"
    p = env.post("/api/admin/newsletter/preview", json={"subject": "S", "text": "**x**"}, headers=h).json()
    assert "<b>x</b>" in p["html"] and p["recipients"] == 0
    assert env.post("/api/admin/newsletter/preview", json={}, headers={**h, "x-ra-csrf": "falsch"}).status_code == 403


def test_admin_user_list_shows_notify(env):  # noqa: F811
    prefs.put("olaf", {"mail": {"releases": False, "newsletter": True, "asked": True, "at": 1_700_000_000_000}})
    h = login(env)
    us = {u["name"]: u for u in env.get("/api/admin/users", headers=h).json()}
    assert us["olaf"]["notify"] == {"releases": False, "newsletter": True, "asked": True}
    assert us["bofh"]["notify"]["asked"] is False
