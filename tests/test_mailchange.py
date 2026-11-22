"""Mailwechsel per Bestaetigungslink (Teilprojekt D, 0.37.6): Modul mailchange."""
import json
import os
import time

import pytest

os.environ.setdefault("DEMO_WARM", "0")
os.environ.setdefault("OSM_AUTOBUILD", "0")

import admin
import auth
import mailchange
import mailer

HOST = "https://riganalyzer.petrick.top"
NEW = "neu@example.org"
OLD = "bofh@example.org"


@pytest.fixture
def mails(data_dir, monkeypatch):
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {
        "bofh": {"hash": h, "apps": ["ride", "rig"], "admin": True, "mail": OLD},
        "olaf": {"hash": h, "apps": ["rig"]},
        "finn": {"hash": h, "apps": ["rig"], "admin": True}}}))   # zweiter Admin: bofh darf gesperrt werden
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    monkeypatch.delenv("MAIL_OFF", raising=False)
    auth._users["mtime"] = None
    sent = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda msg: sent.append(msg))
    return sent


def _wait(mails, n):
    """Die Hinweismail an die alte Adresse laeuft im Hintergrund."""
    for _ in range(100):
        if len(mails) >= n:
            return
        time.sleep(0.02)


def _to(mails, addr):
    return next(m for m in mails if m["To"] == addr)


def _tok(msg):
    return msg.get_content().split("mail-bestaetigen?t=")[1].split()[0]


def _link(mails):
    return _tok(_to(mails, NEW))


def test_request_sends_link_to_new_and_notice_to_old(mails):
    r = mailchange.request("bofh", NEW, HOST, "rig")
    assert r["status"] == "sent" and r["pending"] == NEW and r["expires"] > time.time()
    _wait(mails, 2)
    assert f"{HOST}/mail-bestaetigen?t=" in _to(mails, NEW).get_content()
    old = _to(mails, OLD).get_content()
    assert "n…@example.org" in old and NEW not in old and "mail-bestaetigen" not in old
    assert auth.users()["bofh"]["mail"] == OLD          # noch nicht geaendert


def test_first_mail_without_old_sends_only_link(mails):
    mailchange.request("olaf", "olaf@example.org", HOST, "rig")
    time.sleep(0.1)
    assert [m["To"] for m in mails] == ["olaf@example.org"]


def test_confirm_sets_mail_once(mails):
    mailchange.request("bofh", NEW, HOST, "rig")
    t = _link(mails)
    assert mailchange.confirm(t) == ("bofh", NEW)
    assert auth.users()["bofh"]["mail"] == NEW and mailchange.pending("bofh") is None
    with pytest.raises(mailchange.Invalid):
        mailchange.confirm(t)


def test_same_address_any_case_is_unchanged(mails):
    assert mailchange.request("bofh", "BOFH@example.org", HOST, "rig") == {"status": "unchanged"}
    assert mails == [] and mailchange.pending("bofh") is None


def test_second_request_replaces_first(mails):
    mailchange.request("bofh", NEW, HOST, "rig")
    t1 = _link(mails)
    mails.clear()
    mailchange.request("bofh", NEW, HOST, "rig")
    t2 = _link(mails)
    with pytest.raises(mailchange.Invalid):
        mailchange.confirm(t1)
    assert mailchange.confirm(t2) == ("bofh", NEW)


def test_expired(mails, monkeypatch):
    mailchange.request("bofh", NEW, HOST, "rig")
    t = _link(mails)
    real = time.time
    monkeypatch.setattr(mailchange.time, "time", lambda: real() + 3601)
    assert mailchange.pending("bofh") is None
    with pytest.raises(mailchange.Invalid):
        mailchange.confirm(t)


def test_cancel(mails):
    mailchange.request("bofh", NEW, HOST, "rig")
    t = _link(mails)
    assert mailchange.cancel("bofh") is True and mailchange.cancel("bofh") is False
    assert mailchange.pending("bofh") is None
    with pytest.raises(mailchange.Invalid):
        mailchange.confirm(t)


def test_password_change_invalidates(mails):
    mailchange.request("bofh", NEW, HOST, "rig")
    t = _link(mails)
    admin.set_own_password("bofh", "ganzneu12345")
    with pytest.raises(mailchange.Invalid):
        mailchange.confirm(t)
    assert auth.users()["bofh"]["mail"] == OLD


def test_locked_user_invalidates(mails):
    mailchange.request("bofh", NEW, HOST, "rig")
    t = _link(mails)
    admin.update("bofh", locked=True)
    with pytest.raises(mailchange.Invalid):
        mailchange.confirm(t)


def test_rate_limit_three_per_hour(mails):
    for _ in range(3):
        mailchange.request("bofh", NEW, HOST, "rig")
    with pytest.raises(mailchange.Throttled):
        mailchange.request("bofh", NEW, HOST, "rig")


def test_send_failure_leaves_nothing(mails, monkeypatch):
    def boom(msg):
        raise OSError("weg")
    monkeypatch.setattr(mailer, "_smtp_send", boom)
    with pytest.raises(mailchange.SendFailed):
        mailchange.request("bofh", NEW, HOST, "rig")
    assert mailchange.pending("bofh") is None


def test_notice_failure_does_not_block(mails, monkeypatch):
    def only_new(msg):
        if msg["To"] == OLD:
            raise OSError("alte Adresse weg")
        mails.append(msg)
    monkeypatch.setattr(mailer, "_smtp_send", only_new)
    assert mailchange.request("bofh", NEW, HOST, "rig")["status"] == "sent"
    assert mailchange.pending("bofh")["mail"] == NEW


def test_mail_off_counts_as_sent(mails, monkeypatch):
    monkeypatch.setenv("MAIL_OFF", "1")
    assert mailchange.request("bofh", NEW, HOST, "rig")["status"] == "sent"
    assert mailchange.pending("bofh")["mail"] == NEW and mails == []


@pytest.mark.parametrize("bad", ["kein-at", "", "   "])
def test_invalid_address_raises(mails, bad):
    with pytest.raises(admin.AdminError):
        mailchange.request("bofh", bad, HOST, "rig")
    assert mails == []


def test_remove_clears_and_notifies_old(mails):
    mailchange.request("bofh", NEW, HOST, "rig")
    _wait(mails, 2)
    mails.clear()
    mailchange.remove("bofh", "rig")
    _wait(mails, 1)
    assert auth.users()["bofh"].get("mail", "") == "" and mailchange.pending("bofh") is None
    assert [m["To"] for m in mails] == [OLD] and "entfernt" in mails[0].get_content()


def test_drop(mails):
    mailchange.request("bofh", NEW, HOST, "rig")
    t = _link(mails)
    mailchange.drop("bofh")
    with pytest.raises(mailchange.Invalid):
        mailchange.confirm(t)


def test_store_has_no_plain_token(mails, data_dir):
    mailchange.request("bofh", NEW, HOST, "rig")
    assert _link(mails) not in (data_dir / "mailchange.json").read_text()


def test_mask():
    assert mailchange.mask("neu@example.org") == "n…@example.org"


def test_admin_override_wins_against_concurrent_confirm(mails):
    """Admin setzt die Mail, waehrend eine Bestaetigung auf den Schreib-Lock wartet -> Admin gewinnt."""
    import threading
    mailchange.request("bofh", NEW, HOST, "rig")
    t = _link(mails)
    out = {}

    def run():
        try:
            out["r"] = mailchange.confirm(t)
        except mailchange.Invalid:
            out["r"] = "invalid"
    with auth.WRITE_LOCK:
        th = threading.Thread(target=run)
        th.start()
        time.sleep(0.2)                      # confirm laeuft bis zum Lock
        admin.update("bofh", mail="vomadmin@example.org")
        mailchange.drop("bofh")
    th.join(5)
    assert out["r"] == "invalid" and auth.users()["bofh"]["mail"] == "vomadmin@example.org"



def test_file_is_private(mails, data_dir):
    """mailchange.json enthaelt Passwort-Hashes -> nur fuer den Besitzer lesbar (wie users.json)."""
    import stat
    mailchange.request("olaf", "neu@example.org", "https://x")
    mode = stat.S_IMODE(os.stat(os.path.join(str(data_dir), "mailchange.json")).st_mode)
    assert mode == 0o600
