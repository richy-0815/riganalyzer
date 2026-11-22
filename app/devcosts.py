"""Entwicklungskosten je Login (seit v0.39.0): DATA_DIR/devcosts.json vom Rueckmeldungs-Abgleich auf dem Mac
(ci/rueckmeldungen_sync.py). Die App liest die Datei nur – geschrieben wird sie ausschliesslich vom Abgleich.

Format: {"stand": ISO, "tickets": [{"nr", "titel", "status", "feedback_id", "login", "app",
                                   "laeufe": [{"zeit", "usd", "modell"}], "repo" (optional, Alt-Tickets)}]}
Ein Lauf zaehlt im Monat seiner Zeit. Richtwerte, keine Abrechnung."""

import json
import os


def _path():
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "devcosts.json")


def load() -> dict | None:
    """Inhalt oder None (fehlt, kaputt oder unerwartete Form)."""
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("tickets"), list):
        return None
    if not all(isinstance(t, dict) and isinstance(t.get("laeufe", []), list)
               and all(isinstance(lf, dict) for lf in t.get("laeufe", [])) for t in d["tickets"]):
        return None
    return d


def ticket_fuer(feedback_id: str, d: dict | None = None) -> dict | None:
    """Ticket zur Rueckmeldung; d = schon geladener Inhalt (eine Liste -> nur einmal lesen)."""
    d = d if d is not None else load()
    for t in (d or {}).get("tickets", []):
        if t.get("feedback_id") == feedback_id:
            return {"nr": t.get("nr"), "status": t.get("status")}
    return None


def _usd(x) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0 else 0.0


def monat(ym: str, usd_eur: float) -> dict | None:
    """Summen je Login fuer den Monat JJJJ-MM (Logins nach Euro absteigend), None ohne gueltige devcosts.json."""
    d = load()
    if d is None:
        return None
    logins: dict[str, dict] = {}
    for t in d["tickets"]:
        usd = sum(_usd(lf.get("usd")) for lf in t.get("laeufe", []) if str(lf.get("zeit", "")).startswith(ym))
        if usd <= 0:
            continue
        lg = logins.setdefault(str(t.get("login", "?")), {"login": str(t.get("login", "?")), "usd": 0.0, "eur": 0.0, "tickets": []})
        lg["usd"] += usd
        lg["tickets"].append({"nr": t.get("nr"), "titel": t.get("titel", ""), "status": t.get("status"), "app": t.get("app"),
                              "usd": usd, "eur": usd * usd_eur})
    for lg in logins.values():
        lg["eur"] = lg["usd"] * usd_eur
        lg["tickets"].sort(key=lambda t: (int(t["nr"]) if str(t["nr"]).isdigit() else 0, str(t["nr"])))
    return {"stand": d.get("stand"), "logins": sorted(logins.values(), key=lambda x: -x["eur"])}
