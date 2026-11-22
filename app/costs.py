"""Kostenuebersicht je Rig-Account (seit v0.34.0): Preistabelle (DATA_DIR/costs/prices.json) und reine Rechnung
fuer drei Szenarien (home, home_s3, cloud). Richtwerte, keine Abrechnung."""

import calendar
import datetime as dt
import json
import math
import os
import tempfile

DEFAULTS = {
    "strom_ct": 33.90, "server_w": 6, "proxy_w": 3, "hw_eur": 3.00, "up_mbit": 10, "down_mbit": 100,
    "s3_name": "Hetzner Object Storage", "s3_base_eur": 6.49, "s3_incl_gb": 1024, "s3_egress_incl_gb": 1024,
    "s3_extra_eur_tb": 8.70, "s3_egress_eur_tb": 1.00,
    "cloud_name": "Hetzner CAX41 (32 GB)", "cloud_eur": 31.49,
    "abo_eur": 4.99, "marge_pct": 30, "fee_pct": 1.5, "fee_fix_eur": 0.25,
    "usd_eur": 0.92,   # Entwicklungskosten (Claude, in USD) -> Euro
}
_TEXT = ("s3_name", "cloud_name")
_HOURS = 730
_SCENARIOS = ("home", "home_s3", "cloud")


def _path():
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "costs", "prices.json")


def load_prices() -> dict:
    p = dict(DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            p.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return p


def save_prices(p: dict) -> dict:
    if not isinstance(p, dict):
        raise ValueError("Preistabelle muss ein Objekt sein.")
    for k, v in p.items():
        if k not in DEFAULTS:
            raise ValueError(f"Unbekannter Preisschlüssel: {k}")
        if k in _TEXT:
            if not isinstance(v, str) or len(v) > 80:
                raise ValueError(f"{k}: Text mit höchstens 80 Zeichen erwartet.")
        elif isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ValueError(f"{k}: Zahl größer oder gleich 0 erwartet.")
    out = load_prices()
    out.update(p)
    d = os.path.dirname(_path())
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        os.replace(tmp, _path())
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return out


def fee(amount: float, p: dict) -> float:
    if amount <= 0:
        return 0.0
    return amount * p["fee_pct"] / 100 + p["fee_fix_eur"]


def _c(u, key):
    v = u.get(key, 0)
    return v if isinstance(v, (int, float)) else 0


def compute(users: dict, apps: dict, prices: dict, ym: str | None = None) -> dict:
    if not ym:
        ym = dt.date.today().strftime("%Y-%m")
    y, m = int(ym[:4]), int(ym[5:7])
    dim = calendar.monthrange(y, m)[1]
    users = users or {}
    n = sum(1 for u in users.values() if u.get("days"))
    home_fixed = (prices["server_w"] + prices["proxy_w"]) * _HOURS / 1000 * prices["strom_ct"] / 100 + prices["hw_eur"]
    fixed = {"home": home_fixed, "home_s3": home_fixed + prices["s3_base_eur"],
             "cloud": prices["cloud_eur"] + prices["s3_base_eur"]}
    tot_stor = sum(_c(u, "storage_gbd") for u in users.values() if u.get("days")) / dim
    tot_dl = sum(_c(u, "dl_bytes") for u in users.values() if u.get("days")) / 1e9
    over_stor = max(0.0, tot_stor - prices["s3_incl_gb"]) / 1000 * prices["s3_extra_eur_tb"]
    over_dl = max(0.0, tot_dl - prices["s3_egress_incl_gb"]) / 1000 * prices["s3_egress_eur_tb"]
    sum_stor_gbd = sum(_c(u, "storage_gbd") for u in users.values() if u.get("days"))
    sum_dl = sum(_c(u, "dl_bytes") for u in users.values() if u.get("days"))
    sum_build = sum(_c(u, "build_bytes") for u in users.values() if u.get("days"))
    marge = prices["marge_pct"] / 100
    abo, abo_fee = prices["abo_eur"], fee(prices["abo_eur"], prices)
    rig = sorted(login for login, a in (apps or {}).items() if "rig" in (a or []))
    out = {}
    for sc in _SCENARIOS:
        F = fixed[sc]
        accs = []
        for login in rig:
            u = users.get(login) or {}
            active = bool(u.get("days"))
            dl_b, st = _c(u, "dl_bytes"), _c(u, "storage_gbd")
            share = F / n if (active and n) else 0.0
            var = 0.0
            if active and sc != "home":
                if sum_stor_gbd > 0:
                    var += over_stor * st / sum_stor_gbd
                if sum_dl > 0:
                    var += over_dl * dl_b / sum_dl
            cost = share + var
            price = cost * (1 + marge)
            price = price + fee(price, prices)
            accs.append({
                "name": login, "active": active,
                "usage": {"dl_gb": dl_b / 1e9, "storage_gb": (st / _c(u, "snaps") if _c(u, "snaps") > 0 else 0.0), "analyses": _c(u, "analyses"),
                          "plans": _c(u, "plans"), "routes": _c(u, "routes")},
                "share": share, "variable": var, "cost": cost,
                "profit_abo": (abo - abo_fee - cost) if active else 0.0,
                "price_payg": price if active else 0.0,
                "profit_payg": cost * marge,
            })
        accs.sort(key=lambda a: (not a["active"], -a["cost"]))
        act = [a for a in accs if a["active"]]
        avg_var = sum(a["variable"] for a in act) / len(act) if act else 0.0
        net = abo - abo_fee - avg_var
        be = math.ceil(F / net) if net > 0 else None
        totals = {"cost": sum(a["cost"] for a in accs), "revenue_abo": abo * len(act),
                  "profit_abo": sum(a["profit_abo"] for a in accs),
                  "profit_payg": sum(a["profit_payg"] for a in accs), "break_even": max(1, be) if be is not None else None}
        up_bytes = sum_dl if sc == "home" else (sum_build if sc == "home_s3" else 0)
        upload_h = up_bytes * 8 / (prices["up_mbit"] * 1e6) / 3600 if prices["up_mbit"] else 0.0
        for a in accs:
            for k in ("share", "variable", "cost", "profit_abo", "price_payg", "profit_payg"):
                a[k] = round(a[k], 2)
            a["usage"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in a["usage"].items()}
        out[sc] = {"fixed": round(F, 2), "active": n, "upload_h": upload_h,
                   "totals": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in totals.items()},
                   "accounts": accs}
    return out
