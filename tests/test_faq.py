"""0.37.0: Häufige Fragen (/faq.html) aus faq/*.md – je App gefiltert, nur mit Login, HTML im
Markdown wird nie ungefiltert ausgegeben (Defense in depth), Link im Footer und in „So geht's“."""
import json
import os
import re

import pytest

import auth

HERE = os.path.dirname(__file__)
APP = os.path.join(HERE, "..", "app")
REPO = os.path.join(HERE, "..")
RIDE, RIG = "https://rideanalyzer.petrick.top", "https://riganalyzer.petrick.top"


def fm(app, body, zielgruppe="öffentlich"):
    return f"---\nzielgruppe: {zielgruppe}\napp: {app}\nzuletzt_geprueft: 2026-11-07\n---\n{body}"


@pytest.fixture
def faqdir(tmp_path, monkeypatch):
    d = tmp_path / "faq"
    d.mkdir()
    (d / "01-konto.md").write_text(fm("beide", "# Konto\n\n## Wie komme ich rein?\n\nÜber {{name}}: Registrieren.\n"), encoding="utf-8")
    (d / "02-ride.md").write_text(fm("ride", "# Nur Ride\n\n## Was ist P1?\n\nSolo.\n"), encoding="utf-8")
    (d / "03-rig.md").write_text(fm("rig", "# Nur Rig\n\n## Was ist ein Fahrzeugprofil?\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"),
                                 encoding="utf-8")
    monkeypatch.setenv("FAQ_DIR", str(d))
    return d


@pytest.fixture
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    h = auth.hash_pw("geheim12345")
    (data_dir / "users.json").write_text(json.dumps({"users": {"mia": {"hash": h, "apps": ["ride", "rig"]}}}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setattr(auth, "KEY_FILE", str(data_dir / "session.key"))
    auth._users["mtime"] = None
    auth._fails.clear()
    import main

    def make(host, login=True):
        c = TestClient(main.app, base_url=host)
        o = {"origin": host, "user-agent": "Mozilla/5.0"}
        if login:
            r = c.post("/login", data={"user": "mia", "password": "geheim12345"}, headers=o, follow_redirects=False)
            assert r.status_code == 303
        return c, o
    return make


# ---- Filter je App
def test_ride_shows_ride_and_beide(faqdir):
    import main
    page = main._faq_html(False)
    assert "Wie komme ich rein?" in page and "Was ist P1?" in page
    assert "Fahrzeugprofil" not in page and "Nur Rig" not in page
    assert "Über RideAnalyzer: Registrieren." in page


def test_rig_shows_rig_and_beide(faqdir):
    import main
    page = main._faq_html(True)
    assert "Wie komme ich rein?" in page and "Was ist ein Fahrzeugprofil?" in page
    assert "Was ist P1?" not in page and "Nur Ride" not in page
    assert "Über RigAnalyzer: Registrieren." in page
    assert "<table>" in page and "<td>2</td>" in page


def test_toc_links_every_question(faqdir):
    import main
    page = main._faq_html(True)
    toc = page[page.index('class="card faq-toc"'):page.index("</nav>")]
    for q in ("Wie komme ich rein?", "Was ist ein Fahrzeugprofil?"):
        m = re.search(r'id="([^"]+)">' + re.escape(q) + "<", page)
        assert m, q
        assert f'href="#{m.group(1)}"' in toc, q
    ids = re.findall(r'id="([^"]+)"', page)
    assert len(ids) == len(set(ids)), "doppelte Anker"


def test_invalid_front_matter_not_shown(faqdir):
    import main
    (faqdir / "04-intern.md").write_text(fm("beide", "# Intern\n\n## Geheim?\n\nja\n", zielgruppe="intern"), encoding="utf-8")
    (faqdir / "05-ohne-app.md").write_text("---\nzielgruppe: öffentlich\n---\n# Ohne App\n", encoding="utf-8")
    (faqdir / "06-kaputt.md").write_text("# Kein Kopf\n", encoding="utf-8")
    page = main._faq_html(False)
    assert "Geheim?" not in page and "Ohne App" not in page and "Kein Kopf" not in page


# ---- kein HTML aus dem Repo ungefiltert
EVIL = """# Böse

## Skript?

<script>alert(1)</script>

<div onclick="alert(2)">Block</div>

Inline <img src=x onerror=alert(3)> und <a href="javascript:alert(4)">Link</a>.

[Markdown-Link](javascript:alert(5)) · [ok](https://www.openstreetmap.org) · [intern](/konto) · <https://example.org>

&lt;b&gt;schon escaped&lt;/b&gt;
"""


@pytest.mark.parametrize("rig", [False, True])
def test_html_in_markdown_is_escaped(faqdir, rig):
    import main
    (faqdir / "09-boese.md").write_text(fm("beide", EVIL), encoding="utf-8")
    page = main._faq_html(rig)
    body = page[page.index('<article class="card faq-sec">'):page.rindex('</article>')]   # nur der Markdown-Teil
    assert "<script" not in body.lower()
    assert not re.search(r"<(div|img)\b", body)
    assert not re.search(r"<[^>]*\son\w+\s*=", body)                         # kein echtes Tag mit Event-Handler
    assert not re.search(r'<[^>]*(href|src)="\s*javascript', body, re.I)   # in echten Tags; als Text darf es stehen
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body                   # sichtbar als Text, nicht ausgefuehrt
    assert 'href="https://www.openstreetmap.org"' in body and 'href="/konto"' in body
    assert 'href="https://example.org"' in body


@pytest.mark.parametrize("url", ["//evil.example", "/\\evil.example", "javascript:x", " javascript:x", "data:text/html,x"])
def test_unsafe_links_lose_href(url):
    import faq
    out = faq.render(f"[x]({url})\n", "t", "X")[0]
    assert "href" not in out, out


def test_attribute_allowlist():
    import faq
    out = faq.render("## Frage\n\n| a | b |\n|:-|-:|\n| 1 | 2 |\n\n![Bild](javascript:x)\n", "t", "X")[0]
    assert "javascript" not in out and "<img" not in out
    assert re.search(r'<h3 id="t-frage">Frage</h3>', out)


# ---- Login, Links, Version
def test_faq_needs_login(client, faqdir):
    c, o = client(RIDE, login=False)
    r = c.get("/faq.html", headers=o, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    c, o = client(RIG)
    r = c.get("/faq.html", headers=o)
    assert r.status_code == 200 and "Was ist ein Fahrzeugprofil?" in r.text and "Was ist P1?" not in r.text
    assert c.get("/faq", headers=o).status_code == 200


@pytest.mark.parametrize("folder", ["static-ride", "static-rig"])
def test_links_in_footer_and_help(folder):
    with open(os.path.join(APP, folder, "index.html"), encoding="utf-8") as f:
        html = f.read()
    foot = html[html.index('<footer class="foot">'):html.index("</footer>")]
    help_ = html[html.index('<dialog class="help"'):html.index("</dialog>")]
    assert '<a href="/faq.html">Häufige Fragen</a>' in foot
    assert 'href="/faq.html"' in help_ and "Häufige Fragen" in help_


def test_subpages_footer_links_faq(faqdir):
    import main
    for rig in (False, True):
        assert '<a href="/faq.html">Häufige Fragen</a>' in main._page(rig, "x", "y", "z")


def test_release_and_feature():
    rel = json.load(open(os.path.join(APP, "releases.json"), encoding="utf-8"))
    e = next(r for r in rel if r["version"] == "0.37.0")   # nicht mehr die neueste Version
    assert e["app"] == "beide" and e["public"] and e["public_text"]
    assert any(it[0] == "neu" for it in e["items"])
    ft = json.load(open(os.path.join(APP, "features.json"), encoding="utf-8"))
    assert any(t == "Häufige Fragen" for sec in ft["common"] for t, _ in sec["items"])


# ---- echter Inhalt (faq/ im Repo)
def test_real_faq_found_and_split():
    import faq
    import main
    assert os.path.samefile(faq.faq_dir(), os.path.join(REPO, "faq")) or \
        os.path.isdir(os.path.join(APP, "faq"))
    ride, rig = main._faq_html(False), main._faq_html(True)
    assert ride.count('class="card faq-sec"') >= 4 and rig.count('class="card faq-sec"') >= 5
    for page in (ride, rig):
        assert "Zugang" in page and "Meine Daten" in page and "Rückmeldung" in page and "Passwort" in page
    assert "Fahrzeugprofil" in rig and "Reisepaket" in rig and "Umweltzone" in rig
    assert "Profil" in ride and "Kurven" in ride and "Vergleich" in ride


# ---- Ablage: die Kopie app/faq/ (Container-Bau) bleibt aus Git
def test_faq_copy_is_gitignored():
    with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
        assert "app/faq/" in f.read().splitlines()
