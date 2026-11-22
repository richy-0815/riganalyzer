import json

import pytest

import costs

P = dict(costs.DEFAULTS)


def _fixed_home():
    return (6 + 3) * 730 / 1000 * 0.339 + 3.00


def test_fixed_home():
    r = costs.compute({"a": {"app": "rig", "days": ["2026-11-01"]}}, {"a": ["rig"]}, P)
    assert r["home"]["fixed"] == pytest.approx(5.23, abs=0.01) and r["home"]["active"] == 1


def test_fixed_s3_and_cloud():
    r = costs.compute({"a": {"app": "rig", "days": ["d"]}}, {"a": ["rig"]}, P)
    assert r["home_s3"]["fixed"] == pytest.approx(r["home"]["fixed"] + 6.49, abs=0.005)
    assert r["cloud"]["fixed"] == pytest.approx(31.49 + 6.49, abs=0.005)


def test_share_counts_ride_but_lists_only_rig():
    users = {"r": {"app": "ride", "days": ["d"]}, "g": {"app": "rig", "days": ["d"]}}
    r = costs.compute(users, {"r": ["ride"], "g": ["rig"]}, P)["cloud"]
    assert r["active"] == 2 and [a["name"] for a in r["accounts"]] == ["g"]
    assert r["accounts"][0]["share"] == pytest.approx(37.98 / 2, abs=0.01)


def test_profit_abo_and_payg():
    r = costs.compute({"g": {"app": "rig", "days": ["d"]}}, {"g": ["rig"]}, P)["home"]
    a = r["accounts"][0]
    c = _fixed_home()
    assert costs.fee(4.99, P) == pytest.approx(0.32, abs=0.005)
    assert a["cost"] == pytest.approx(c, abs=0.01)
    assert a["profit_abo"] == pytest.approx(4.99 - costs.fee(4.99, P) - c, abs=0.01)
    assert a["profit_payg"] == pytest.approx(c * 0.30, abs=0.01)
    assert a["price_payg"] == pytest.approx(c * 1.30 + costs.fee(c * 1.30, P), abs=0.01)


def test_fee_zero_amount():
    assert costs.fee(0, P) == 0


def test_overage_split_by_share():
    p = dict(P, s3_egress_incl_gb=1, s3_egress_eur_tb=1000)
    users = {"a": {"app": "rig", "days": ["d"], "dl_bytes": 3e9},
             "b": {"app": "rig", "days": ["d"], "dl_bytes": 1e9}}
    r = costs.compute(users, {"a": ["rig"], "b": ["rig"]}, p)
    acc = {x["name"]: x for x in r["cloud"]["accounts"]}
    assert acc["a"]["variable"] == pytest.approx(2.25, abs=0.005)
    assert acc["b"]["variable"] == pytest.approx(0.75, abs=0.005)
    s3 = {x["name"]: x for x in r["home_s3"]["accounts"]}
    assert s3["a"]["variable"] == pytest.approx(2.25, abs=0.005)
    assert all(x["variable"] == 0 for x in r["home"]["accounts"])


def test_storage_overage_uses_days_in_month():
    p = dict(P, s3_incl_gb=0, s3_extra_eur_tb=1000)
    users = {"a": {"app": "rig", "days": ["d"], "storage_gbd": 30.0}}
    r = costs.compute(users, {"a": ["rig"]}, p, "2026-11")  # 30 Tage -> 1 GB -> 1,00 EUR
    assert r["cloud"]["accounts"][0]["variable"] == pytest.approx(1.0, abs=0.005)


def test_no_activity():
    r = costs.compute({}, {"g": ["rig"]}, P)
    for s in r.values():
        assert s["active"] == 0 and s["accounts"][0]["share"] == 0
        assert s["totals"]["break_even"] >= 1


def test_inactive_listed_last_with_zero_share():
    users = {"a": {"app": "rig", "days": ["d"], "dl_bytes": 1e9},
             "b": {"app": "rig", "days": ["d"]}}
    r = costs.compute(users, {"z": ["rig"], "a": ["rig"], "b": ["rig"]}, dict(P, s3_egress_incl_gb=0))
    names = [a["name"] for a in r["cloud"]["accounts"]]
    assert names == ["a", "b", "z"]
    z = r["cloud"]["accounts"][2]
    assert z["active"] is False and z["share"] == 0 and z["cost"] == 0


def test_upload_hours():
    r = costs.compute({"a": {"app": "rig", "days": ["d"], "dl_bytes": 4.5e9}}, {"a": ["rig"]}, P)
    assert r["home"]["upload_h"] == pytest.approx(1.0)
    assert r["home_s3"]["upload_h"] == 0 and r["cloud"]["upload_h"] == 0
    r = costs.compute({"a": {"app": "rig", "days": ["d"], "build_bytes": 4.5e9}}, {"a": ["rig"]}, P)
    assert r["home_s3"]["upload_h"] == pytest.approx(1.0)
    assert r["home"]["upload_h"] == 0 and r["cloud"]["upload_h"] == 0


def test_load_prices_fills_missing(data_dir):
    (data_dir / "costs").mkdir()
    (data_dir / "costs" / "prices.json").write_text(json.dumps({"abo_eur": 5.99}))
    p = costs.load_prices()
    assert p["abo_eur"] == 5.99 and p["marge_pct"] == 30


def test_save_prices_validates(data_dir):
    assert costs.load_prices() == costs.DEFAULTS
    for bad in ({"nope": 1}, {"abo_eur": -1}, {"s3_name": "x" * 81}, {"abo_eur": "x"}):
        with pytest.raises(ValueError):
            costs.save_prices(bad)
    out = costs.save_prices({"abo_eur": 6.0})
    assert out["abo_eur"] == 6.0 and costs.load_prices()["abo_eur"] == 6.0


def test_storage_gb_is_average_occupied_over_snapshot_days():
    users = {"a": {"app": "rig", "days": ["d"], "storage_gbd": 4.0, "snaps": 2},
             "b": {"app": "rig", "days": ["d"], "storage_gbd": 1.0}}
    acc = {x["name"]: x for x in costs.compute(users, {"a": ["rig"], "b": ["rig"]}, P, "2026-11")["home"]["accounts"]}
    assert acc["a"]["usage"]["storage_gb"] == pytest.approx(2.0) and acc["b"]["usage"]["storage_gb"] == 0


def test_break_even_none_when_abo_does_not_cover_fee_and_variable():
    r = costs.compute({"a": {"app": "rig", "days": ["d"]}}, {"a": ["rig"]}, dict(P, abo_eur=0.1))
    assert all(s["totals"]["break_even"] is None for s in r.values())
    r = costs.compute({"a": {"app": "rig", "days": ["d"]}}, {"a": ["rig"]}, P)
    assert r["home"]["totals"]["break_even"] >= 1
