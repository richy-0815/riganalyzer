"""Meine Daten (seit 0.36.0): alles, was zu einem Login gespeichert oder gezaehlt ist.

- export(user): vollstaendiger Download (DSGVO Art. 15/20), ueber beide Apps, je Login
- overview(user, app): kurze Uebersicht fuer „Mein Konto“, nur Begriffe der eigenen App (Ride/Rig getrennt)
Nie Passwort-Hash, 2FA-Geheimnis, Backup-Codes – Kontofelder per Erlaubnisliste, nicht per Sperrliste.
"""

import datetime as dt

import activity
import admin
import auth
import feedback
import hooks
import mailchange
import prefs
import usage

ACCOUNT_FIELDS = ("mail", "apps", "group", "registered", "last_login", "pw_changed", "pw_initial", "pending", "locked",
                  "note", "settings")


def _account(user):
    u = auth.users().get(user, {})
    out = {"name": user, **{k: u[k] for k in ACCOUNT_FIELDS if k in u}}
    out["admin"] = admin.rights_text(admin._admin_out(u))
    out["zweiter_faktor"] = bool(u.get("totp_secret"))   # nur ob, nie das Geheimnis
    p = mailchange.pending(user)   # offener Mailwechsel: Adresse + Ablauf, nie Token/Pruefwert
    if p:
        out["mail_wechsel"] = {"mail": p["mail"],
                               "gueltig_bis": dt.datetime.fromtimestamp(p["expires"]).isoformat(timespec="seconds")}
    return out


def _feedback(user):
    with feedback._lock:
        return [x for x in feedback._load() if x.get("user") == user]


def _usage(user):
    login = usage._key(user)
    return {ym: u for ym in usage.months() if (u := usage.month(ym)["users"].get(login))}


def export(user):
    return {"erstellt": dt.datetime.now().isoformat(timespec="seconds"), "login": user,
            "hinweis": "Alles, was RideAnalyzer/RigAnalyzer zu diesem Login gespeichert oder gezählt hat. "
                       "Passwort und 2FA-Schlüssel sind nur als Prüfwert gespeichert und hier bewusst nicht enthalten.",
            "konto": _account(user), "vorlieben": prefs.get(user),
            **{k: v for fn in hooks.all_("mydata.export") for k, v in fn(user).items()},
            "rueckmeldungen": _feedback(user), "verbrauch": _usage(user),
            "anmeldungen": activity.logins(user), "aktivitaet": activity.of(user)}


# ---------------------------------------------------------------- Uebersicht fuers Konto
def _de(x, d=1):
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _n(n, one, many):
    return f"{n} {one if n == 1 else many}"


MONTH_ALL = (("analyses", "Analysen", None), ("plans", "Planungen", None))


def _fmt(v, unit):
    if unit == "gb":
        return f"{_de(v / 1e9)} GB"
    if unit == "min":
        return f"{_de(v)} Min."
    if unit == "gbd":
        return f"{_de(v)} GB-Tage"
    return str(int(v))


def overview(user, app):
    acc = _account(user)
    pr = prefs.get(user)
    stored = [{"label": "Konto", "value": ", ".join(x for x in (
        acc.get("mail") or "keine Mailadresse", f"seit {acc['registered']}" if acc.get("registered") else "",
        "neue Adresse wartet auf Bestätigung" if acc.get("mail_wechsel") else "") if x)},
        {"label": "Einstellungen und Vorlieben", "value": _n(len(acc.get("settings", {})) + len(pr), "Eintrag", "Einträge")}]
    for fn in hooks.all_("mydata.overview"):
        stored += fn(user, app, pr)
    stored.append({"label": "Rückmeldungen", "value": str(sum(1 for x in _feedback(user) if x.get("app") == app))})

    ym = dt.date.today().strftime("%Y-%m")
    u = usage.month(ym)["users"].get(usage._key(user), {})
    werte = [{"label": "aktive Tage", "value": str(len(u.get("days", [])))}]
    werte += [{"label": lab, "value": _fmt(u.get(k, 0), unit)} for k, lab, unit in MONTH_ALL + tuple(f for fn in hooks.all_("mydata.month") for f in fn(app))]
    logins = [{"t": e["t"], "ip": e["ip"], "device": e["device"], "ok": e["ok"]} for e in activity.logins(user)]
    return {"gespeichert": stored, "monat": {"ym": ym, "werte": werte}, "anmeldungen": logins}
