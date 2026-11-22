"""0.45.0 (#3): Touren teilen – Einladungen mit festem Stand, nur Logins, Annehmen nötig."""
import datetime as dt
import json
import os

import pytest

import auth
import shares
import trips

T0 = dt.datetime(2026, 11, 12, 10, 0, 0)
STOPS = [{"lat": 52.5, "lon": 13.4, "name": "Berlin"}, {"lat": 51.7, "lon": 14.3, "name": "Cottbus"}]


@pytest.fixture
def env(data_dir, monkeypatch):
    users = {"olaf": {"hash": "x", "apps": ["rig"], "mail": "olaf@example.org"},
             "otto": {"hash": "x", "apps": ["rig"], "mail": "otto@example.org"},
             "rita": {"hash": "x", "apps": ["ride"]},
             "lena": {"hash": "x", "apps": ["rig"], "locked": True}}
    (data_dir / "users.json").write_text(json.dumps({"users": users}))
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    auth._users["mtime"] = None
    return data_dir


def _tour(user="olaf", name="Lausitz"):
    return trips.save(user, {"name": name, "stops": STOPS, "note": "privat", "plan": {"pts": 2}})


def test_teilen_annehmen_ergibt_kopie_ohne_notiz(env):
    t = _tour()
    assert shares.invite("olaf", "otto", t["id"], now=T0)
    box = shares.inbox("otto", now=T0)
    assert len(box) == 1 and box[0]["from"] == "olaf" and box[0]["name"] == "Lausitz"
    assert "plan" not in box[0] and not isinstance(box[0].get("stops"), list)
    got = shares.accept("otto", box[0]["id"], now=T0)
    mine = trips.list_("otto")
    assert len(mine) == 1 and mine[0]["id"] == got["id"]
    assert got["name"] == "Lausitz" and got["stops"] == t["stops"] and got["plan"] == {"pts": []}   # bereinigt
    assert "note" not in got and got["shared_from"]["user"] == "olaf"
    assert trips.get("olaf", t["id"]) == t


def test_fester_stand(env):
    t = _tour()
    shares.invite("olaf", "otto", t["id"], now=T0)
    trips.save("olaf", {"id": t["id"], "name": "Anders", "stops": STOPS[::-1]})
    trips.delete("olaf", t["id"])
    got = shares.accept("otto", shares.inbox("otto", now=T0)[0]["id"], now=T0)
    assert got["name"] == "Lausitz" and got["stops"] == t["stops"]


def test_nicht_zustellbar_gleich_und_still(env):
    t = _tour()
    for to in ("niemand", "rita", "lena", "olaf"):
        assert shares.invite("olaf", to, t["id"], now=T0) is None
        assert shares.inbox(to, now=T0) == []
    out = shares.outbox("olaf", now=T0)
    assert sorted(x["to"] for x in out) == ["lena", "niemand", "olaf", "rita"]
    assert {x["state"] for x in out} == {"offen"}


def test_name_normalisiert(env):
    t = _tour()
    shares.invite("olaf", " Otto ", t["id"], now=T0)
    assert len(shares.inbox("otto", now=T0)) == 1


def test_doppelt_annehmen(env):
    t = _tour()
    shares.invite("olaf", "otto", t["id"], now=T0)
    sid = shares.inbox("otto", now=T0)[0]["id"]
    shares.accept("otto", sid, now=T0)
    with pytest.raises(shares.NotFound):
        shares.accept("otto", sid, now=T0)
    assert len(trips.list_("otto")) == 1


def test_fremde_tour_und_ids(env):
    t = _tour()
    with pytest.raises(shares.NotFound):
        shares.invite("otto", "olaf", t["id"], now=T0)          # nicht seine Tour
    shares.invite("olaf", "otto", t["id"], now=T0)
    sid = shares.inbox("otto", now=T0)[0]["id"]
    with pytest.raises(shares.NotFound):
        shares.withdraw("otto", sid, now=T0)
    with pytest.raises(shares.NotFound):
        shares.accept("olaf", sid, now=T0)
    with pytest.raises(shares.NotFound):
        shares.decline("olaf", sid, now=T0)
    with pytest.raises(shares.NotFound):
        shares.accept("otto", "0" * 16, now=T0)
    with pytest.raises(shares.NotFound):
        shares.accept("otto", "../x", now=T0)


def test_zurueckziehen_und_ablehnen(env):
    t = _tour()
    shares.invite("olaf", "otto", t["id"], now=T0)
    sid = shares.inbox("otto", now=T0)[0]["id"]
    shares.withdraw("olaf", sid, now=T0)
    assert shares.inbox("otto", now=T0) == []
    assert shares.outbox("olaf", now=T0)[0]["state"] == "zurückgezogen"
    t2 = _tour(name="Zwei")
    shares.invite("olaf", "otto", t2["id"], now=T0)
    shares.decline("otto", shares.inbox("otto", now=T0)[0]["id"], now=T0)
    assert {x["name"]: x["state"] for x in shares.outbox("olaf", now=T0)}["Zwei"] == "abgelehnt"
    assert trips.list_("otto") == []


def test_ablauf_14_tage(env):
    t = _tour()
    shares.invite("olaf", "otto", t["id"], now=T0)
    sid = shares.inbox("otto", now=T0)[0]["id"]
    later = T0 + dt.timedelta(days=15)
    assert shares.inbox("otto", now=later) == []
    assert shares.outbox("olaf", now=later)[0]["state"] == "abgelaufen"
    with pytest.raises(shares.NotFound):
        shares.accept("otto", sid, now=later)
    assert shares.outbox("olaf", now=T0 + dt.timedelta(days=46)) == []


def test_bremse_pro_tag(env):
    t = _tour()
    for i in range(shares.PER_DAY):
        shares.invite("olaf", f"x{i}", t["id"], now=T0)
    with pytest.raises(shares.TooMany):
        shares.invite("olaf", "x99", t["id"], now=T0)


def test_bremse_offene(env):
    t = _tour()
    n = 0
    for day in range(3):
        for i in range(shares.PER_DAY if n + shares.PER_DAY <= shares.MAX_OPEN else shares.MAX_OPEN - n):
            shares.invite("olaf", f"y{day}-{i}", t["id"], now=T0 + dt.timedelta(days=day))
            n += 1
    assert n == shares.MAX_OPEN
    with pytest.raises(shares.TooMany):
        shares.invite("olaf", "zuviel", t["id"], now=T0 + dt.timedelta(days=3))


def test_gleiche_einladung_ersetzt(env):
    t = _tour()
    shares.invite("olaf", "otto", t["id"], now=T0)
    shares.invite("olaf", "otto", t["id"], now=T0)
    assert len(shares.inbox("otto", now=T0)) == 1
    assert len(shares.outbox("olaf", now=T0)) == 1


def test_forget_user(env):
    t = _tour()
    shares.invite("olaf", "otto", t["id"], now=T0)
    shares.invite("olaf", "niemand", t["id"], now=T0)
    assert shares.forget_user("otto") == 1
    assert [x["to"] for x in shares.outbox("olaf", now=T0)] == ["niemand"]


def test_of_user_ohne_stand(env):
    t = _tour()
    shares.invite("olaf", "otto", t["id"], now=T0)
    d = shares.of_user("otto")
    assert len(d["erhalten"]) == 1 and d["gesendet"] == []
    assert "snapshot" not in d["erhalten"][0]
    assert len(shares.of_user("olaf")["gesendet"]) == 1


def test_datei_0600(env):
    t = _tour()
    shares.invite("olaf", "otto", t["id"], now=T0)
    path = os.path.join(os.environ["DATA_DIR"], "shares.json")
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


# ---------------------------------------------------------------- Randfaelle
def test_wiederholen_zaehlt_und_meldet_erneuert(env):
    # dieselbe Einladung immer wieder schicken darf weder die Bremse umgehen noch Mails auslösen
    t = _tour()
    first = shares.invite("olaf", "otto", t["id"], now=T0)
    assert first and not first.get("renewed")
    again = shares.invite("olaf", "otto", t["id"], now=T0)
    assert again and again["renewed"] is True
    for _ in range(shares.PER_DAY - 2):
        shares.invite("olaf", "otto", t["id"], now=T0)
    with pytest.raises(shares.TooMany):
        shares.invite("olaf", "otto", t["id"], now=T0)
    assert len(shares.inbox("otto", now=T0)) == 1


def test_plan_wird_bereinigt(env):
    # plan kommt beim Empfänger von jemand anderem – nur bekannte, einfache Felder gehen mit
    plan = {"v": 1, "mode": "fast", "nights": "5", "skip": ["a", 3], "avoided": ["de-berlin"], "lez": True, "toll": False,
            "ferry": "x", "evil": "<img src=x>",
            "pts": [{"lat": 52.5, "lon": 13.4, "name": "Berlin", "stop": False, "heading": 90, "mode": "noway",
                     "camp": {"flags": {"image": "javascript:alert(1)"}}, "poi": {"osm": "node/1"}, "x": 1},
                    {"lat": "52", "lon": 13.4, "name": "kaputt"},
                    {"lat": 51.7, "lon": 14.3, "name": "C" * 500, "stop": True}]}
    t = trips.save("olaf", {"name": "P", "stops": STOPS, "plan": plan})
    shares.invite("olaf", "otto", t["id"], now=T0)
    got = shares.accept("otto", shares.inbox("otto", now=T0)[0]["id"], now=T0)["plan"]
    assert set(got) <= {"v", "mode", "nights", "skip", "avoided", "lez", "toll", "ferry", "pts"}
    assert "evil" not in got and "ferry" not in got and got["lez"] is True
    assert got["pts"][0] == {"lat": 52.5, "lon": 13.4, "name": "Berlin", "stop": False, "heading": 90, "mode": "noway"}
    assert len(got["pts"]) == 2 and len(got["pts"][1]["name"]) <= 120 and got["pts"][1]["stop"] is True


def test_users_cli_del_raeumt_auf(env, monkeypatch):
    # Login per users.py löschen – sonst sähe ein neuer gleichnamiger Login alte Einladungen
    import users
    t = _tour()
    shares.invite("olaf", "otto", t["id"], now=T0)
    assert users.main(["users.py", "del", "otto"]) == 0
    assert shares.outbox("olaf", now=T0) == []
