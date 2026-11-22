"""Aktualitaets-Test: oeffentliche Texte gegen Sperrliste, Versionsversprechen und Planungsgebiet."""
import json
import os
import re

import pytest
import yaml

HERE = os.path.dirname(__file__)
APP = os.path.join(HERE, "..", "app")
PLAN = os.path.join(HERE, "..", "plan-regionen.yml")

_PROMISE = re.compile(r"\b(?:folgt|folgen|kommt|kommen)\s+(?:mit|ab|in)\s+(?:Version\s+|v)?(\d+)\.(\d+)")
_PROMISE_NAVI = re.compile(r"\b(?:folgt|folgen|kommt|kommen)\s+(?:mit|ab|in)\s+RigNavi\s+v?(\d+)\.(\d+)")
_NAVI = re.compile(r"RigNavi\s+(\d+)\.(\d+)")
_REGION_NAMES = {"germany": "Deutschland", "austria": "Österreich", "switzerland": "Schweiz",
                 "denmark": "Dänemark", "norway": "Norwegen", "sweden": "Schweden"}


def _strip(html):
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _releases():
    with open(os.path.join(APP, "releases.json"), encoding="utf-8") as f:
        return json.load(f)


def public_texts():
    """Name -> gerenderter Text ohne HTML-Tags, je App (Ride/Rig)."""
    import main
    out = {}
    for rig, app in ((False, "Ride"), (True, "Rig")):
        out[f"{app}: Login"] = _strip(main._login_html(rig))
        out[f"{app}: Info"] = _strip(main._INFO[rig])
        out[f"{app}: Funktionen"] = _strip(main._features_html(rig))
        out[f"{app}: Häufige Fragen"] = _strip(main._faq_html(rig))   # FAQ aus faq/
        out[f"{app}: Technik"] = _strip(main._tech_html(rig))
        out[f"{app}: App"] = _strip(main._app_html(rig))
        folder = "static-rig" if rig else "static-ride"
        with open(os.path.join(APP, folder, "index.html"), encoding="utf-8") as f:
            out[f"{app}: index.html"] = _strip(f.read())
    newest = next((e for e in _releases() if e.get("public") and e.get("public_text")), None)
    if newest:
        out[f"releases.json {newest['version']} public_text"] = _strip(newest["public_text"])
    return out


def _context(text, i, n):
    return "…" + text[max(0, i - 30):i + n + 30] + "…"


def find_stale(texts, phrases):
    hits = []
    for name, text in texts.items():
        for p in phrases:
            i = text.find(p["text"])
            if i >= 0:
                hits.append(f"{name}: „{p['text']}“ ({p.get('warum', '')}) bei {_context(text, i, len(p['text']))[:70 + len(p['text'])]}")
    return hits


def _ver(s):
    a, b = s.split(".")[:2]
    return int(a), int(b)


def find_outdated_promises(texts, version, navi_version):
    cur, navi = _ver(version), navi_version
    hits = []
    for name, text in texts.items():
        for m in _PROMISE.finditer(text):
            if (int(m.group(1)), int(m.group(2))) <= cur:
                hits.append(f"{name}: Versionsversprechen schon erfuellt (aktuell {version}): {_context(text, m.start(), len(m.group(0)))}")
        for m in _PROMISE_NAVI.finditer(text):
            if (int(m.group(1)), int(m.group(2))) <= navi:
                hits.append(f"{name}: RigNavi-Versprechen schon erfuellt (aktuell {navi[0]}.{navi[1]}): {_context(text, m.start(), len(m.group(0)))}")
    return hits


def highest_navi(releases):
    best = (0, 0)
    for e in releases:
        for s in [e.get("title", "")] + [it[1] for it in e.get("items", [])] + [e.get("public_text", "")]:
            for m in _NAVI.finditer(s):
                best = max(best, (int(m.group(1)), int(m.group(2))))
    return best


def missing_area_names(regions, area):
    need = set()
    for r in regions:
        if r in _REGION_NAMES:
            need.add(_REGION_NAMES[r])
        elif r.startswith(("france/", "spain/")):
            need.add("Korridor")
    return sorted(n for n in need if n not in area)


def _phrases():
    with open(os.path.join(HERE, "stale_phrases.json"), encoding="utf-8") as f:
        return json.load(f)


# ---- Regeln selbst (rot/gruen am Beispiel)
def test_rule_stale_phrase_detected_and_ok():
    ph = [{"text": "Prototyp", "warum": "Alpha"}]
    bad = find_stale({"Seite X": "Das ist noch ein Prototyp der Karte"}, ph)
    assert bad and "Seite X" in bad[0] and "Prototyp" in bad[0] and "ein Prototyp der" in bad[0]
    assert not find_stale({"Seite X": "Alles Alpha"}, ph)


@pytest.mark.parametrize("text,bad", [
    ("Beta folgt mit v0.9", True), ("kommen mit v0.35 dazu", True), ("folgt ab 0.10", True),
    ("folgt mit v0.99", False), ("Beta folgt mit Version 1.0.0", False), ("Beta folgt mit Version 0.9", True), ("kommt in v1.0", False),
    ("wurde mit v0.9 gebracht", False)])
def test_rule_version_promise(text, bad):
    assert bool(find_outdated_promises({"S": text}, "0.35.5", (0, 17))) is bad


def test_rule_version_promise_version_wording_is_checked():
    # "Version 1.0.0" wird erkannt (nicht uebersehen), ist aber erlaubt, weil 1.0 > aktuell
    assert _PROMISE.search("Beta folgt mit Version 1.0.0")
    assert not find_outdated_promises({"S": "Beta folgt mit Version 1.0.0"}, "0.35.5", (0, 17))


@pytest.mark.parametrize("text,bad", [
    ("Offline folgt mit RigNavi 0.9", True), ("kommt in RigNavi 0.17 dazu", True),
    ("kommt mit RigNavi 0.18", False), ("RigNavi 0.9 bringt Offline", False)])
def test_rule_navi_promise(text, bad):
    assert bool(find_outdated_promises({"S": text}, "0.35.5", (0, 17))) is bad


def test_rule_area_names():
    regs = ["germany", "france/alsace", "liechtenstein", "andorra", "norway"]
    assert missing_area_names(regs, "Deutschland, Norwegen und der Korridor") == []
    assert missing_area_names(regs, "Deutschland und Norwegen") == ["Korridor"]
    assert missing_area_names(["spain/cataluna", "sweden"], "Schweden") == ["Korridor"]
    assert missing_area_names(["liechtenstein", "andorra"], "") == []


def test_rule_highest_navi():
    rel = [{"title": "x", "items": [["neu", "RigNavi 0.17.1: foo", "rig"]]}, {"title": "RigNavi 0.9 da", "items": []}]
    assert highest_navi(rel) == (0, 17)


# ---- echter Stand
@pytest.fixture
def texts(data_dir):
    return public_texts()


def test_no_stale_phrases(texts):
    hits = find_stale(texts, _phrases())
    assert not hits, "\n".join(hits)


def test_no_outdated_version_promises(texts, data_dir):
    import main
    hits = find_outdated_promises(texts, main.VERSION, highest_navi(_releases()))
    assert not hits, "\n".join(hits)


def test_plan_area_covers_routing_regions():
    with open(PLAN, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    miss = missing_area_names(d["rideanalyzer_plan_regions"], d["rideanalyzer_plan_area"])
    assert not miss, f"rideanalyzer_plan_area nennt nicht: {miss}"


def test_real_texts_are_nonempty(texts):
    assert len(texts) >= 10 and all(len(t) > 50 for t in texts.values())
