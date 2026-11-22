"""Tempo-Tabelle je Land (seit v0.10): eine Quelle fuer RigNavi und die Webseite."""
import json
import os

from test_admin import HOST, basic, env, login  # noqa: F401  (Fixture env)

APP = os.path.join(os.path.dirname(__file__), "..", "app")
CLASSES = ("car", "heavy", "trailer", "trailer100")
KINDS = ("motorway", "expressway", "rural", "urban")


def _rules():
    with open(os.path.join(APP, "speedrules.json"), encoding="utf-8") as f:
        return json.load(f)


def test_schema():
    r = _rules()
    assert r["version"] == 1
    assert set(r["countries"]) == {"DE", "AT", "CH", "FR", "IT", "ES"}
    for cc, c in r["countries"].items():
        assert c["name"] and c["source"].startswith("http") and c["as_of"], cc
        assert set(c["limits"]) == set(CLASSES), cc
        for cls in CLASSES:
            assert set(c["limits"][cls]) == set(KINDS), (cc, cls)
            for v in c["limits"][cls].values():
                assert v is None or (isinstance(v, int) and 30 <= v <= 130), (cc, cls, v)


def test_de_trailer():
    de = _rules()["countries"]["DE"]["limits"]
    assert de["trailer"]["motorway"] == 80
    assert de["trailer100"]["motorway"] == 100
    assert de["car"]["motorway"] is None


def test_api_requires_login(env):  # noqa: F811 – Fixture (Import oben)
    assert env.get("/api/speedrules", headers={"user-agent": "okhttp/5"}).status_code == 401
    r = env.get("/api/speedrules", headers=basic("olaf"))
    assert r.status_code == 200 and r.json() == _rules()
