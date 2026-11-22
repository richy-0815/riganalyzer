"""Anmeldungen -> Telegram (0.31.1): Login-Seite immer, Apps/Cookie nur einmal je 6 h, abschaltbar."""
import pytest

import loginwatch


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(loginwatch, "_send", lambda subj, body: out.append((subj, body)))
    monkeypatch.setattr(loginwatch, "_seen", {})
    monkeypatch.setenv("LOGIN_NOTIFY", "1")
    return out


def test_login_and_failed(sent):
    loginwatch.login("richy", "rig", "1.2.3.4")
    loginwatch.login("mallory", "rig", "6.6.6.6", ok=False)
    assert "richy hat sich bei RigAnalyzer angemeldet" in sent[0][0] and "1.2.3.4" not in sent[0][1]   # keine IP an Telegram
    assert sent[1][0].startswith("⚠️") and "mallory" in sent[1][0]


def test_app_once_per_6h_and_after_login_no_double(sent, monkeypatch):
    loginwatch.app_seen("olaf", "rig", "1.1.1.1", "okhttp/5.5")
    loginwatch.app_seen("olaf", "rig", "1.1.1.1", "okhttp/5.5")
    loginwatch.app_seen("olaf", "rig", "1.1.1.1", "RigNavi/4 CFNetwork/3826 Darwin/25")
    assert [s for s, _ in sent] == ["📱 olaf nutzt RigNavi Android – RigAnalyzer", "📱 olaf nutzt RigNavi iPad/iPhone – RigAnalyzer"]
    sent.clear()
    loginwatch.login("richy", "rig", "1.2.3.4")
    loginwatch.app_seen("richy", "rig", "1.2.3.4", "browser")     # gleiche Sitzung direkt danach: still
    assert len(sent) == 1
    monkeypatch.setattr(loginwatch.time, "time", lambda: 10 ** 10)
    loginwatch.app_seen("richy", "rig", "1.2.3.4", "browser")     # Tage spaeter wieder da
    assert sent[-1][0].startswith("👀 richy ist wieder da")


def test_off(sent, monkeypatch):
    monkeypatch.setenv("LOGIN_NOTIFY", "0")
    loginwatch.login("richy", "rig", "1.2.3.4")
    loginwatch.app_seen("richy", "rig", "1.2.3.4", "okhttp")
    assert sent == []
