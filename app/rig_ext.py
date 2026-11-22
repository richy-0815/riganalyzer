"""RigAnalyzer-Erweiterung des Kerns (seit 0.46.4): meldet die Rig-Teile an den Erweiterungspunkten an (hooks.py).

- app.router.rig       Rig-Endpunkte (rig_routes.rig) – nur in der Rig-App
- analyze.vehicle      Hoehen, Breiten, Gewichte, Steigungen, Laender (rig.collect)
- analyze.extra_pois   Laeden an der Route (shops.route_pois)
- purge.user           Touren, Navi-Pakete, Einladungen, Platz-Bewertungen eines geloeschten Logins
- mydata.export/…      dieselben Daten in „Meine Daten“ (Download und Konto-Uebersicht)
- engine.files, cache.stamp   Fingerabdruck/Datenstand fuer Zwischenspeicher und Beispiele (rig.py, shops.py, Einkaufsdaten)
- app.bodylimit_large.rig  Obergrenze fuer gespeicherte Analysen
- app.start            unterbrochenen Paketbau aufnehmen, DATAtourisme-Abruf (Frankreich)
"""

import logging
import os
import sqlite3
import threading
import time

import hooks
import jobqueue
import package
import platzlaenge
import purge
import reviews
import rig
import rig_routes
import shares
import shops
import trips

RIG_APP = "rig"
log = logging.getLogger("rideanalyzer")


# ---------------------------------------------------------------- Endpunkte
hooks.add("app.router.rig", rig_routes.rig)


# ---------------------------------------------------------------- Analyse
hooks.add("analyze.vehicle", rig.collect)
hooks.add("analyze.extra_pois", shops.route_pois)
hooks.add("engine.files", lambda: ("rig.py", "shops.py", "sondermaut.py", "sondermaut.json"))   # Fingerabdruck der Analyse: neu rechnen, wenn die sich aendern


def _shops_stamp():
    sdb = shops.get()   # Einkaufsdaten-Stand fuer Zwischenspeicher und Beispiele
    return (sdb.meta.get("built") if sdb else None) or "none"


hooks.add("cache.stamp", _shops_stamp)

# Gespeicherte Analysen sind gross (viele Etappen): eigene Obergrenze fuer den Anfrage-Koerper
hooks.add("app.bodylimit_large.rig", {"size": trips.MAX_ANALYSIS + 1024 * 1024, "prefix": ("/api/trips/",), "suffix": ("/analysis",)})


# ---------------------------------------------------------------- Start
def _frcamps_loop():
    """DATAtourisme (Stell-/Campingplaetze Frankreich, seit v0.24.0): beim Start und taeglich nachsehen,
    neu laden wenn aelter als 7 Tage. Fehler: alter Stand bleibt, morgen wieder."""
    import frcamps
    jobqueue.lower_priority(15)
    time.sleep(90)
    while True:
        try:
            if frcamps.due():
                frcamps.refresh_isolated()   # eigener Prozess: blockiert die App nicht
        except Exception:
            log.exception("DATAtourisme-Abruf abgebrochen")
        time.sleep(24 * 3600)


def _start():
    package.recover()   # Paketbau, der beim letzten Neustart unterbrochen wurde
    platzlaenge.start()   # Stellplatz-Laenge: Warteschlange vom letzten Lauf weiter abarbeiten
    if os.environ.get("FRCAMPS_REFRESH", "1") == "1":
        threading.Thread(target=_frcamps_loop, daemon=True, name="frcamps").start()


hooks.add("app.start", _start)
hooks.add("nights.camp_length", platzlaenge.night_verdict)   # Nachtplatz-Vorschlaege: Laenge aus Website/Luftbild


# ---------------------------------------------------------------- Login geloescht
def _purge(name, step):
    step("Touren", lambda: purge._rmtree(os.path.join(purge._data(), "trips", name)))
    step("Navi-Pakete", lambda: purge._rmtree(os.path.join(purge._data(), "packages", name)))
    step("Einladungen", lambda: shares.forget_user(name))

    def rev():
        if not os.path.isfile(reviews.FILE):
            return False
        with reviews._db() as con:
            return con.execute("UPDATE reviews SET user = ? WHERE user = ?", (purge.gone(name), name)).rowcount > 0
    step("Bewertungen", lambda: purge._safe_sql(rev))


hooks.add("purge.user", _purge)


# ---------------------------------------------------------------- Meine Daten
def _reviews(user):
    if not os.path.isfile(reviews.FILE):
        return []
    try:
        with reviews._db() as con:
            rows = con.execute("SELECT * FROM reviews WHERE user = ? ORDER BY at DESC", (user,)).fetchall()
    except sqlite3.Error:
        return []
    return [reviews._row(r) for r in rows]


def _trips(user):
    out = []
    for t in trips.list_(user):
        out.append({"trip": t, "fruehere_staende": trips.versions(user, t["id"]),
                    "gespeicherte_analyse": trips.analysis_info(user, t["id"]),
                    "navi_paket": package.status(user, t["id"])})
    return out


def _export(user):
    """Gilt ueber beide Apps (Download = alles zum Login), deshalb ohne App-Pruefung."""
    return {"touren": _trips(user), "platz_bewertungen": _reviews(user), "geteilte_touren": shares.of_user(user)}


def _overview(user, app, pr):
    import mydata
    if app != RIG_APP:
        return []
    n, de = mydata._n, mydata._de
    ts = _trips(user)
    vers = sum(len(t["fruehere_staende"]) for t in ts)
    pk = [t["navi_paket"] for t in ts if t["navi_paket"]["state"] == "ready"]
    prof = (pr.get("profiles") or {}).get("profiles") or {}
    sh = shares.of_user(user)
    return [{"label": "Fahrzeugprofile", "value": n(len(prof), "Profil", "Profile") + (f" ({', '.join(prof)})" if prof else "")},
            {"label": "Touren", "value": n(len(ts), "Tour", "Touren") + f", {n(vers, 'früherer Stand', 'frühere Stände')}"},
            {"label": "Navi-Pakete", "value": n(len(pk), "Paket", "Pakete") + (f" · {de(sum(p['size'] for p in pk) / 1e9)} GB" if pk else "")},
            {"label": "Platz-Bewertungen", "value": str(len(_reviews(user)))},
            {"label": "Geteilte Touren", "value": f"{len(sh['gesendet'])} gesendet, {len(sh['erhalten'])} erhalten"}]


MONTH_RIG = (("routes", "Navi-Routen", None), ("dl_bytes", "Navi-Downloads", "gb"), ("build_min", "Paket-Bau", "min"),
             ("storage_gbd", "Speicher", "gbd"))

hooks.add("mydata.export", _export)
hooks.add("mydata.overview", _overview)
hooks.add("mydata.month", lambda app: MONTH_RIG if app == RIG_APP else ())
