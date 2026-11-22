"""Neue Funktion in releases.json => features.json muss mitgezogen werden."""
import importlib.util
from pathlib import Path

_P = Path(__file__).resolve().parents[1] / "werkzeuge" / "check_features_touched.py"
_spec = importlib.util.spec_from_file_location("check_features_touched", _P)
cft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cft)


def rel(version, *kinds):
    return {"version": version, "date": "2026-11-06", "app": "beide", "title": "t",
            "public": False, "items": [[k, "text", "both"] for k in kinds]}


OLD = [rel("0.35.5", "fix")]


def test_neu_ohne_features():
    assert cft.needs_features(OLD, [rel("0.36.0", "neu", "fix")] + OLD) is True


def test_nur_besser_fix():
    assert cft.needs_features(OLD, [rel("0.35.6", "besser", "fix")] + OLD) is False


def test_geaenderter_alter_eintrag():
    assert cft.needs_features([rel("0.35.0", "neu")], [rel("0.35.0", "neu", "neu")]) is False


def test_leerer_alter_stand():
    assert cft.needs_features([], [rel("0.1.0", "neu")]) is True


def test_new_feature_version():
    assert cft.new_feature_version(OLD, [rel("0.36.0", "neu"), rel("0.35.6", "fix")] + OLD) == "0.36.0"
    assert cft.new_feature_version(OLD, [rel("0.35.6", "fix")] + OLD) is None
