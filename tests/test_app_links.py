"""0.15.2: Ride bleibt Ride, Rig bleibt Rig – Mails, Links, Seiten."""
import mailer
import reset
from test_group_admin import env, login  # noqa: F401  (Fixture env, Ride-Host)


def catch(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda msg: sent.append(msg))
    return sent


def test_sender_name(monkeypatch):
    sent = catch(monkeypatch)
    mailer.send("a@b.de", "x", "y", app="ride")
    mailer.send("a@b.de", "x", "y", app="rig")
    assert [m["From"].split("<")[0].strip().strip('"') for m in sent] == ["RideAnalyzer", "RigAnalyzer"]


def test_access_mail_follows_new_logins_apps(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    import main
    monkeypatch.setattr(main, "_in_background", lambda fn, *a: fn(*a))
    sent = catch(monkeypatch)
    c, h = login()                                    # bofh auf dem Ride-Host – neue Logins bekommen Ride
    r = c.post("/api/admin/users", json={"name": "finn", "mail": "finn@example.org", "send_mail": True}, headers=h)
    assert r.json()["mail_ok"] is True
    ride = sent[0].get_content()
    assert sent[0]["Subject"] == "Dein Zugang zum RideAnalyzer" and "https://rideanalyzer.petrick.top" in ride and "riganalyzer" not in ride
    subject, rig, app = main._access_mail("ria", "pw", ["rig"], False)
    assert subject == "Dein Zugang zum RigAnalyzer" and "https://riganalyzer.petrick.top" in rig and "rideanalyzer" not in rig and app == "rig"
    _, both, _ = main._access_mail("bea", "pw", ["ride", "rig"], False)
    assert "https://rideanalyzer.petrick.top" in both and "https://riganalyzer.petrick.top" in both


def test_reset_mail_names_the_app(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    sent = catch(monkeypatch)
    import auth
    d = auth.read_disk()
    d["otto"]["mail"] = "otto@example.org"
    auth.save_users(d)
    reset.request("otto", "https://rideanalyzer.petrick.top", app="ride")
    assert sent[0]["Subject"] == "Neues Passwort für den RideAnalyzer" and "RideAnalyzer" in sent[0]["From"]


def test_pages_link_only_own_app(env):  # noqa: F811 – Fixture (Import oben)
    c, h = login()
    for path in ("/releases.html", "/features.html", "/faq.html"):
        assert "riganalyzer.petrick.top" not in c.get(path, headers=h).text
    assert c.get("/app.html", headers=h, follow_redirects=False).status_code == 404


RIDE_HOST, RIG_HOST = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"
import re  # noqa: E402

OTHER = {RIDE_HOST: re.compile(r"RigAnalyzer|RigNavi|\bRig\b|Wohnmobil|Gespann|Umweltzone"),
         RIG_HOST: re.compile(r"RideAnalyzer|\bRide\b|Motorrad")}


def test_public_pages_never_mention_the_other_app(env):  # noqa: F811 – Fixture (Import oben)
    """mia hat beide Apps, ist kein Admin: was ein normaler Nutzer sieht, nennt nur die eigene App."""
    from fastapi.testclient import TestClient
    import main
    for host, bad in OTHER.items():
        c = TestClient(main.app, base_url=host)
        o = {"origin": host, "user-agent": "Mozilla/5.0"}
        assert c.get("/login", headers=o).status_code == 200 and not bad.search(c.get("/login", headers=o).text)
        assert c.post("/login", data={"user": "mia", "password": "geheim12345"}, headers=o, follow_redirects=False).status_code == 303
        for path in ("/", "/releases.html", "/features.html", "/faq.html", "/tech.html", "/info.html", "/konto"):
            text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", c.get(path, headers=o).text, flags=re.S)
            hit = bad.search(text)
            assert not hit, f"{host}{path}: …{text[max(0, hit.start() - 60):hit.end() + 30]}…"
