"""Seed der QA-Abnahmetest-Logins (e2e_seed.py): nur STAGE=qa, idempotent, fasst Fremd-Logins nicht an."""
import json

import bcrypt
import pytest

import auth
import e2e_seed
import feedback

PW, TOTP = "e2e-geheim-123", "JBSWY3DPEHPK3PXP"
LOGINS = {"e2e-ride", "e2e-rig", "e2e-beide", "e2e-gruppe", "e2e-admin"}


def test_frischer_stand():
    users, groups = e2e_seed.seed({}, {}, PW, TOTP)
    assert set(users) == LOGINS
    gid = [g for g, v in groups.items() if v["name"] == "E2E"]
    assert len(gid) == 1
    assert users["e2e-ride"]["apps"] == ["ride"] and users["e2e-rig"]["apps"] == ["rig"]
    assert users["e2e-beide"]["apps"] == ["ride", "rig"]
    assert users["e2e-gruppe"]["admin"] == {"apps": ["ride", "rig"], "groups": gid}
    assert users["e2e-admin"]["admin"] is True
    assert users["e2e-admin"]["totp_secret"] == TOTP and users["e2e-admin"]["totp_backup"] == []
    assert users["e2e-gruppe"]["totp_secret"] == TOTP and users["e2e-gruppe"]["totp_backup"] == []
    for n, u in users.items():
        assert bcrypt.checkpw(PW.encode(), u["hash"].encode())
        assert u["mail"] == "e2e@localhost"
        if n not in ("e2e-admin", "e2e-gruppe"):
            assert "totp_secret" not in u


def test_idempotent():
    u1, g1 = e2e_seed.seed({}, {}, PW, TOTP)
    u2, g2 = e2e_seed.seed(json.loads(json.dumps(u1)), json.loads(json.dumps(g1)), PW, TOTP)
    assert (u1, g1) == (u2, g2)


def test_passwortwechsel():
    u1, g1 = e2e_seed.seed({}, {}, PW, TOTP)
    u2, _ = e2e_seed.seed(u1, g1, "anderes-pw-9999", TOTP)
    assert bcrypt.checkpw(b"anderes-pw-9999", u2["e2e-ride"]["hash"].encode())


def test_fremde_unveraendert_und_neu_entfernt():
    fremd = {"bofh": {"hash": "x", "apps": ["ride"], "admin": True}, "e2e-neu-1": {"hash": "y", "apps": ["ride"]},
             "e2e-neu-abc": {"hash": "y", "apps": ["rig"]},
             "e2eneu1731000000": {"hash": "y", "apps": ["ride"], "pending": True}}
    gfremd = {"g1": {"name": "XJR-Club"}}
    users, groups = e2e_seed.seed(json.loads(json.dumps(fremd)), dict(gfremd), PW, TOTP)
    assert users["bofh"] == fremd["bofh"]
    assert not [n for n in users if n.startswith(("e2e-neu-", "e2eneu"))]
    assert groups["g1"] == gfremd["g1"]


def test_main_nur_qa(monkeypatch, data_dir, capsys):
    monkeypatch.setenv("STAGE", "prod")
    monkeypatch.setenv("E2E_PASSWORD", PW)
    monkeypatch.setenv("E2E_TOTP_SECRET", TOTP)
    with pytest.raises(SystemExit) as e:
        e2e_seed.main()
    assert e.value.code == 2
    assert not (data_dir / "users.json").exists()


def test_main_qa(monkeypatch, data_dir, capsys):
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setenv("STAGE", "qa")
    monkeypatch.setenv("E2E_PASSWORD", PW)
    monkeypatch.setenv("E2E_TOTP_SECRET", TOTP)
    e2e_seed.main()
    assert "geaendert" in capsys.readouterr().out
    assert set(json.loads((data_dir / "users.json").read_text())["users"]) == LOGINS
    assert (data_dir / "groups.json").exists()
    e2e_seed.main()
    out = capsys.readouterr().out
    assert "unveraendert" in out and PW not in out


def test_clean_feedback_nur_testlogins():
    items = [{"id": "1", "user": "e2e-ride"}, {"id": "2", "user": "bofh"}, {"id": "3", "user": "e2eneu1731000000"},
             {"id": "4", "user": "e2e-admin"}, {"id": "5", "user": "e2efreund"}]
    assert [x["id"] for x in e2e_seed.clean_feedback(items)] == ["2", "5"]


def test_main_raeumt_rueckmeldungen(monkeypatch, data_dir, capsys):
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("STAGE", "qa")
    monkeypatch.setenv("E2E_PASSWORD", PW)
    monkeypatch.setenv("E2E_TOTP_SECRET", TOTP)
    e2e_seed.main()
    fb = [{"id": "a", "t": "2026-11-07T10:00:00", "user": "e2e-ride", "app": "ride", "kind": "idee", "text": "x", "done": True},
          {"id": "b", "t": "2026-11-07T10:00:00", "user": "bofh", "app": "rig", "kind": "lob", "text": "y", "done": False}]
    (data_dir / "feedback.json").write_text(json.dumps(fb))
    capsys.readouterr()
    e2e_seed.main()
    assert "geaendert" in capsys.readouterr().out
    assert [x["id"] for x in feedback._load()] == ["b"]
    e2e_seed.main()
    assert "unveraendert" in capsys.readouterr().out


def test_main_raeumt_mailwechsel_der_testlogins(monkeypatch, data_dir, capsys):
    """Sonst sammelt jeder QA-Lauf einen Zeitstempel der Mengenbremse (3/h) -> ab dem 4. Lauf 429."""
    monkeypatch.setattr(auth, "USERS_FILE", str(data_dir / "users.json"))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("STAGE", "qa")
    monkeypatch.setenv("E2E_PASSWORD", PW)
    monkeypatch.setenv("E2E_TOTP_SECRET", TOTP)
    e2e_seed.main()
    (data_dir / "mailchange.json").write_text(json.dumps({
        "pending": {"e2e-ride": {"hash": "h1", "mail": "x@example.org", "exp": 9e9, "pw": "p"},
                    "bofh": {"hash": "h2", "mail": "y@example.org", "exp": 9e9, "pw": "p"}},
        "sent": {"e2e-ride": [1.0, 2.0, 3.0], "bofh": [1.0]}}))
    capsys.readouterr()
    e2e_seed.main()
    assert "geaendert" in capsys.readouterr().out
    d = json.loads((data_dir / "mailchange.json").read_text())
    assert list(d["pending"]) == ["bofh"] and list(d["sent"]) == ["bofh"]
    e2e_seed.main()
    assert "unveraendert" in capsys.readouterr().out
