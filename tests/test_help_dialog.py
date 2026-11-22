"""„So geht's“ in Happen (0.38.5, Ticket #11): Kurzstart + Aufklapper, Grenzen und Datenschutz in den Häufigen Fragen."""
import os
import re

import pytest

APP = os.path.join(os.path.dirname(__file__), "..", "app")
FOLDERS = {"static-ride": False, "static-rig": True}   # Ordner -> rig?
# Ziel: etwa ein Drittel der frueheren Laenge (Ride 577 / Rig 619 Woerter)
MAX_WORDS = {"static-ride": 260, "static-rig": 280}


def dialog(folder):
    with open(os.path.join(APP, folder, "index.html"), encoding="utf-8") as f:
        html = f.read()
    return html[html.index('<dialog class="help"'):html.index("</dialog>")]


def words(html):
    return len(re.sub(r"<[^>]+>", " ", html).split())


@pytest.mark.parametrize("folder", FOLDERS)
def test_only_three_steps_open_rest_folded(folder):
    d = dialog(folder)
    assert d.count('<ol class="steps">') == 1
    outside = re.sub(r"<details.*?</details>", "", d, flags=re.S)
    assert re.findall(r"<h3>(.*?)</h3>", outside) == ["In drei Schritten"]
    folds = re.findall(r'<details class="help-fold">\s*<summary>(.*?)</summary>(.*?)</details>', d, re.S)
    assert len(folds) >= 2
    for title, body in folds:
        assert " open" not in title
        assert 2 <= body.count("<li>") <= 4, title


@pytest.mark.parametrize("folder", FOLDERS)
def test_about_a_third_of_the_old_length(folder):
    assert words(dialog(folder)) <= MAX_WORDS[folder]


@pytest.mark.parametrize("folder", FOLDERS)
def test_limits_and_privacy_moved_to_faq(folder):
    d = dialog(folder)
    assert "<h3>Was es nicht kann</h3>" not in d and "<h3>Daten &amp; Datenschutz</h3>" not in d
    assert "Was wir zählen" not in d and "Was Admins sehen" not in d
    assert 'href="/faq.html#planen-was-kann-die-seite-nicht"' in d
    assert 'href="/faq.html#datenschutz-was-wird-gespeichert"' in d


@pytest.mark.parametrize("folder", FOLDERS)
def test_faq_anchors_exist_in_that_app(folder, monkeypatch):
    import main
    page = main._faq_html(FOLDERS[folder])
    for anchor in re.findall(r'href="/faq\.html#([^"]+)"', dialog(folder)):
        assert f'id="{anchor}"' in page, anchor


def test_faq_has_limits_and_no_longer_points_to_help():
    faq = os.path.join(APP, "..", "faq")
    planen = open(os.path.join(faq, "02-planen.md"), encoding="utf-8").read()
    daten = open(os.path.join(faq, "06-datenschutz.md"), encoding="utf-8").read()
    assert "## Was kann die Seite nicht?" in planen
    assert "So geht's" not in daten


def test_rig_specific_storage_in_rig_faq():
    import main
    rig, ride = main._faq_html(True), main._faq_html(False)
    assert "Fahrzeugprofile" in rig and "Navi-Pakete" in rig
    assert "Navi-Pakete" not in ride
