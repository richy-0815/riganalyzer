"""RideAnalyzer – Prototyp. Web-Oberflaeche + Analyse-Jobs.

GPX hochladen -> Job (Thread) -> Browser fragt Fortschritt ab -> Ergebnis-JSON.
Hochgeladene Dateien werden nicht gespeichert; gecacht werden nur Kartendaten.
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

import activity
import admin
import analyze
import auth
import mailchange
import purge
import mailer
import notify
import qapost
import register
import reset
import twofa
import build_db
import feedback
import lez
import loginwatch
import osmdb
import planner
import overpass
import itertools

from starlette.concurrency import run_in_threadpool
import jobqueue
import resultcache
import regions
import usage
import costs
import devcosts
import faq
import rechtliches
import luftbild
import hooks
import web
from web import access, is_rig, who
from web import client_ip as _client_ip
from web import may_moderate as _may_moderate
from web import scope as _scope

VERSION = "0.54.2"
HERE = os.path.dirname(os.path.abspath(__file__))
# Stand = juengste Datei der App (Code + Oberflaechen), nicht nur analyze.py
STAMP = time.strftime("%d.%m.%Y %H:%M", time.localtime(max(
    os.path.getmtime(os.path.join(d, f)) for sub in ("", "static", "static-ride", "static-rig")
    for d in [os.path.join(HERE, sub)] for f in os.listdir(d) if f.endswith((".py", ".js", ".css", ".html")))))
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "5")) * 1024 * 1024
MAX_JOBS = int(os.environ.get("MAX_JOBS", "2"))
MAX_JOBS_PER_LOGIN = 3   # aktive (wartend + laufend) je Login
SHORT_KM = int(os.environ.get("SHORT_KM", "300"))     # Routen bis hierhin in eigener Spur
SHORT_JOBS = int(os.environ.get("SHORT_JOBS", "1"))   # 0 = keine kurze Spur
LIVE_JOBS = int(os.environ.get("LIVE_JOBS", "1"))
OSM_DB = os.environ.get("OSM_DB", "/data/osm.sqlite")          # alte Sammel-DB
OSM_DIR = os.environ.get("OSM_DIR", "/data/osm")               # je Region eine DB-Datei
OSM_REGIONS = os.environ.get("OSM_REGIONS", "europe/germany/brandenburg").split(",")
OSM_MAX_AGE = float(os.environ.get("OSM_MAX_AGE_DAYS", "30")) * 86400

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
import logredact  # noqa: E402
logredact.install()   # apikey/access_token aus httpx-URLs nie ins Log
log = logging.getLogger("rideanalyzer")
import events  # noqa: E402  (Ereignis-Tagebuch)
import report  # noqa: E402
logging.getLogger().addHandler(events.ErrorHandler(level=logging.WARNING))   # Warnungen/Fehler in den Tagesbericht

# Kern-Endpunkte haengen am Router `core`; create_app() (Dateiende) baut daraus die App.
core = APIRouter()
STAGE = os.environ.get("STAGE", "prod")
GIT_REF = os.environ.get("GIT_REF", "")




lower_priority = jobqueue.lower_priority   # in jobqueue (auch von rig_ext benutzt)


pool = ThreadPoolExecutor(max_workers=MAX_JOBS, thread_name_prefix="lokal", initializer=lower_priority, initargs=(5,))
# Analysen mit Live-Daten (Overpass, langsam/unzuverlaessig) in eigener Spur, damit sie
# lokale Analysen nicht blockieren. Overpass selbst laeuft ohnehin nur einzeln.
live_pool = ThreadPoolExecutor(max_workers=LIVE_JOBS, thread_name_prefix="live", initializer=lower_priority, initargs=(5,))
# kurze Routen (bis SHORT_KM) warten nicht hinter langen Reisen – eigene Spur mit SHORT_JOBS Plaetzen
short_pool = ThreadPoolExecutor(max_workers=max(1, SHORT_JOBS), thread_name_prefix="kurz", initializer=lower_priority, initargs=(5,))
rate = jobqueue.Rate()          # Sekunden je km aus den letzten echten Analysen (fuer die Restzeit)
_seq = itertools.count(1)
POOLS = {"lokal": lambda: pool, "kurz": lambda: short_pool, "live": lambda: live_pool}


# Das uvicorn-Access-Log saehe nur den Reverse-Proxy als Absender – stattdessen loggt die
# Middleware unten echte Client-IP (X-Forwarded-For) und angemeldeten Basic-Auth-User.
logging.getLogger("uvicorn.access").disabled = True


jobs = {}
jobs_lock = threading.Lock()
db_state = {"building": None, "last_error": None, "started": None}
wake = threading.Event()   # neue Region bestellt -> Pflege-Schleife sofort weiter


def _cleanup():
    cutoff = time.time() - 3600
    with jobs_lock:
        for k in [k for k, j in jobs.items() if j["t"] < cutoff]:
            del jobs[k]


def _event_analysis(job, filename, km, sec, options, stages=1):
    user, app = job.get("by", "-").split("@")[0], "rig" if (options or {}).get("rig") else "ride"
    if job.get("demo"):
        events.log("beispiel", user, app, name=filename[:-4] if filename.endswith(".gpx") else filename)
    else:
        events.log("analyse", user, app, name=filename, km=round(km, 1), sec=round(sec, 1), stages=stages)
        usage.add(user, app, analyses=1, analyse_sec=sec)


class Cancelled(Exception):
    """Analyse vom Nutzer abgebrochen (seit 0.28.0) – faellt beim naechsten Fortschritts-Schritt heraus."""


def _after_done(job, res, sec, filename):
    """0.46.0: Messwert fuer die Restzeit + fertiges, vollstaendig lokales Ergebnis zwischenspeichern.
    Fehler hier machen eine fertige Analyse nie kaputt (Review 12.11.2026)."""
    try:
        rate.add(job.get("km", 0), sec)
        if job.get("cache_key") and resultcache.complete(res):
            # Titel aus dem Dateinamen des Hochladers nicht weitergeben – beim Treffer neu setzen
            resultcache.store(job["cache_key"], {**res, "_name_from_file": res.get("name") == filename.rsplit(".", 1)[0]})
    except Exception:
        log.exception("Zwischenspeichern fehlgeschlagen")


def _cache_stamp():
    """Daten-/Codestand fuer den Ergebnis-Zwischenspeicher (wie _demo_key, ohne Beispielname)."""
    db = osmdb.get()
    zm = lez.meta() or {}
    built = (db.meta.get("built"), len(db.regions)) if db else None
    return f"{_ENGINE}|{built}|{_ext_stamp()}|{zm.get('bnzfe')}|{zm.get('osm')}"


def _ext_stamp():
    """Datenstand der App-Erweiterungen (Rig: Einkaufsdaten, Review 12.11.2026) fuer Zwischenspeicher und Beispiele."""
    return "|".join(str(fn()) for fn in hooks.all_("cache.stamp")) or "none"


def _run(job_id, raw, filename, options=None):
    job = jobs[job_id]
    by = job.get("by", "-")
    if job.get("cancel"):   # noch in der Warteschlange abgebrochen
        job.update(state="cancelled", msg="abgebrochen")
        return

    info = {}

    def progress(p, msg):
        if job.get("cancel"):
            raise Cancelled()
        job.update(state="running", progress=round(float(p)), msg=msg)
        if "missing" not in job and info.get("missing"):   # sofort zeigen, nicht erst am Ende
            job["missing"] = _missing_with_eta(info["missing"])

    t0 = time.time()
    try:
        stages, gname = analyze.parse_stages(raw) if (options or {}).get("rig") else ([], "")
        split = None
        if (options or {}).get("rig") and len(stages) < 2:
            stages, gname, split = _split_long(raw)
        if len(stages) >= 2:
            res = _run_trip(stages, gname, filename, progress, info, options)
            if split:
                res["split"] = split
            job.update(state="done", progress=100, msg="fertig", result=res)
            _after_done(job, res, time.time() - t0, filename)
            _demo_store(job, res)
            log.info("Reise %s (%s): %d Etappen, %.0f km in %.1f s", filename, by, len(stages),
                     sum(r["stats"]["km"] for r in res["stages"]), time.time() - t0)
            _event_analysis(job, filename, sum(r["stats"]["km"] for r in res["stages"]), time.time() - t0, options, len(stages))
            return
        res = analyze.analyze(raw, filename, progress, info, options)
        res["file"] = filename
        res["missing"] = _missing_with_eta(res.get("missing") or [])
        job.update(state="done", progress=100, msg="fertig", result=res)
        _after_done(job, res, time.time() - t0, filename)
        _demo_store(job, res)
        log.info("Analyse %s (%s): %s km in %.1f s (%s)", filename, by, res["stats"]["km"], time.time() - t0,
                 res["stats"].get("source"))
        _event_analysis(job, filename, res["stats"]["km"], time.time() - t0, options)
    except Cancelled:
        job.update(state="cancelled", msg="abgebrochen")
        log.info("Analyse %s (%s) abgebrochen nach %.1f s", filename, by, time.time() - t0)
    except (analyze.AnalyzeError, overpass.OverpassError) as e:
        job.update(state="error", msg=str(e))
        if info.get("missing"):
            job["missing"] = _missing_with_eta(info["missing"])
        log.info("Analyse %s (%s) abgelehnt: %s", filename, by, e)
        events.log("analyse_fehler", by.split("@")[0], "rig" if (options or {}).get("rig") else "ride", name=filename, msg=str(e)[:200])
    except Exception as e:
        log.exception("Analyse %s (%s) fehlgeschlagen", filename, by)
        job.update(state="error", msg=f"Interner Fehler ({e.__class__.__name__}).")


# ---------------------------------------------------------------- Beispiele vorberechnen
# Die Beispiel-GPX aendern sich nie; das Ergebnis haengt nur von Code-Version und Kartenstand ab
# (beim Rig rechnet der Browser die Fahrzeugwerte, der Server nicht). Also einmal rechnen,
# in data/demo-cache ablegen und danach sofort ausliefern.
DEMO_CACHE = os.path.join(os.environ.get("DATA_DIR", "/data"), "demo-cache")


def _engine_hash():
    """Fingerabdruck der Analyse (nicht der Oberflaeche): nur wenn sich die aendert, neu rechnen."""
    import hashlib
    h = hashlib.sha1(usedforsecurity=False)
    files = ("analyze.py", "osmdb.py", "dem.py", "overpass.py", "lez.py", "poi.py",
             *(f for fn in hooks.all_("engine.files") for f in fn()))
    for f in files:
        with open(os.path.join(HERE, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


_ENGINE = _engine_hash()


def _demo_key(name, rig):
    import hashlib
    db = osmdb.get()
    path = os.path.join(HERE, "static", "demo", f"{name}.gpx")
    built = (db.meta.get("built"), len(db.regions)) if db else None
    with open(path, "rb") as f:
        h = hashlib.sha1(f.read(), usedforsecurity=False)
    sbuilt = _ext_stamp()
    zm = lez.meta() or {}   # Zonenstand (BNZFE/OSM-Zeitstempel): neuer Stand -> Beispiele neu rechnen
    h.update(f"|{name}|{rig}|{built}|{sbuilt}|{_ENGINE}|{zm.get('bnzfe')}|{zm.get('osm')}".encode())
    return h.hexdigest()[:16]


def _demo_file(name, rig):
    return os.path.join(DEMO_CACHE, f"{name}-{'rig' if rig else 'ride'}.json")


def _demo_load(name, rig):
    try:
        with open(_demo_file(name, rig), encoding="utf-8") as f:
            d = json.load(f)
        return d["result"] if d.get("key") == _demo_key(name, rig) else None
    except (OSError, ValueError, KeyError):
        return None


def _demo_store(job, res):
    if not job.get("demo") or res.get("missing"):   # nur vollstaendig lokale Ergebnisse aufheben
        return
    name, rig = job["demo"]
    try:
        os.makedirs(DEMO_CACHE, exist_ok=True)
        tmp = _demo_file(name, rig) + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": _demo_key(name, rig), "result": res}, f, ensure_ascii=False)
        os.replace(tmp, _demo_file(name, rig))
    except OSError as e:
        log.warning("Beispiel %s nicht gespeichert: %s", name, e)


def _demo_list():
    import re as _re
    out = []
    for folder, rig in (("static-ride", False), ("static-rig", True)):
        if ("rig" if rig else "ride") not in web.served_apps():   # Einzel-App-Container: nur die eigenen Beispiele
            continue
        with open(os.path.join(HERE, folder, "index.html"), encoding="utf-8") as f:
            out += [(n, rig) for n in dict.fromkeys(_re.findall(r'data-demo="(\w+)"', f.read()))]
    return out


# Vorrat-Status: der Container gilt erst als "healthy", wenn alle Beispiele vorberechnet sind
warm_state = {"done": False, "todo": None, "current": None}


def _warm_demos():
    """Nach dem Start alle Beispiele nacheinander vorrechnen – nur wenn sonst nichts laeuft.
    Seit 0.27.1 mit niedrigster Prioritaet: nach einer neuen Analyse-Version sind das 26 komplette Analysen."""
    lower_priority(19)
    demos = _demo_list()
    warm_state["todo"] = sum(1 for n, r in demos if _demo_load(n, r) is None) if osmdb.get() else len(demos)
    if not warm_state["todo"]:
        warm_state["done"] = True
        return
    time.sleep(60)
    for name, rig in demos:
        while osmdb.get() is None or db_state.get("building") or any(
                j["state"] in ("queued", "running") for j in list(jobs.values())):
            time.sleep(30)
        if _demo_load(name, rig) is not None:
            continue
        with open(os.path.join(HERE, "static", "demo", f"{name}.gpx"), "rb") as f:
            raw = f.read()
        if analyze.needs_live(raw):
            continue
        warm_state["current"] = f"{name} ({'rig' if rig else 'ride'})"
        job_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[job_id] = {"state": "queued", "progress": 0, "msg": "vorberechnen", "t": time.time(),
                            "by": "vorrat", "demo": (name, rig)}
        t0 = time.time()
        _run(job_id, raw, f"{name}.gpx", {"rig": rig})
        log.info("Beispiel %s (%s) vorberechnet in %.0f s: %s", name, "rig" if rig else "ride",
                 time.time() - t0, jobs[job_id]["state"])
        with jobs_lock:
            jobs.pop(job_id, None)
        warm_state["todo"] = max(0, (warm_state["todo"] or 1) - 1)
    warm_state.update(done=True, current=None, todo=0)
    log.info("Beispiel-Vorrat vollstaendig – Container ist bereit")


for _start in hooks.all_("app.start"):   # App-Erweiterungen (Rig: unterbrochenen Paketbau aufnehmen, DATAtourisme)
    _start()
activity.start()

if os.environ.get("DEMO_WARM", "1") != "1":
    warm_state["done"] = True
else:
    threading.Thread(target=_warm_demos, daemon=True, name="beispiele").start()

MAX_STAGES = analyze.MAX_STAGES


# ---------------------------------------------------------------- Tagesbericht
REPORT_TO = os.environ.get("REPORT_TO", "alarm@example.org")


def _report_health():
    import shutil
    live = _live_values()
    try:
        free = shutil.disk_usage(os.environ.get("DATA_DIR", "/data")).free / 1e9
    except OSError:
        free = None
    va = live.get("valhalla", "?")
    return {"version": VERSION, "osm": live.get("osm_date", "?"), "disk_free_gb": free,
            "valhalla": "ok" if va and not va.startswith("(") else "nicht erreichbar"}


def _report_space():
    """(heute, gestern) aus dem Host-Skript für den Platz-Bericht (Cron 06:40) – fehlt die Datei, entfaellt der Abschnitt."""
    def load(name):
        try:
            with open(os.path.join(os.environ.get("DATA_DIR", "/data"), name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return load("platz.json"), load("platz-gestern.json")


_usage_backfilled = False


def _usage_tick(now):
    """Verbrauchsbuch pflegen: einmal je Prozess den laufenden Monat (Tage 1..gestern, nur Ereignisse vor dem ersten Live-Eintrag) aus dem Tagebuch nachtragen,
    bei jedem Durchlauf den Tages-Speicherstand (macht hoechstens einmal je Tag Arbeit)."""
    global _usage_backfilled
    try:
        if not _usage_backfilled:
            _usage_backfilled = True
            import datetime as _dt
            evs = []
            for d in range(1, now.day):
                evs += events.read_day(_dt.date(now.year, now.month, d))
            usage.backfill(now.strftime("%Y-%m"), evs)
        usage.storage_snapshot(now)
    except Exception:   # Zusatz, darf die Schleife nie stoppen
        log.exception("Verbrauchsbuch")


def _report_loop():
    """Jeden Morgen ab 07:00 den Bericht ueber gestern – einmal (Merker im Tagebuch), nach Neustart bis 12 Uhr nachgeholt."""
    while True:
        import datetime as _dt
        _usage_tick(_dt.datetime.now())
        try:
            day = report.due(_dt.datetime.now())
            if day:
                events.prune()
                built = report.build(day, events.read_day(day), _report_health(), _report_space())
                if built is None or mailer.send(REPORT_TO, built[0], built[1]):
                    report.mark_sent(day)
                    log.info("Tagesbericht %s %s", day, "verschickt" if built else "entfaellt (kein Betrieb)")
        except Exception:   # der Bericht darf die App nie stoeren
            log.exception("Tagesbericht")
        time.sleep(300)


def _run_trip(stages, gname, filename, progress, info, options):
    """Reise mit Etappen (seit 0.46.6 in analyze.run_trip); hier zusaetzlich Wartezeit fuer fehlende Regionen."""
    return analyze.run_trip(stages, gname, filename, progress, info, options, finish_missing=_missing_with_eta)


def _check_limits(by):
    """Unter jobs_lock aufrufen: 10 aktive gesamt, MAX_JOBS_PER_LOGIN je Login (Ticket #16)."""
    active = sum(1 for j in jobs.values() if j["state"] in ("queued", "running"))
    if active >= 10:
        raise HTTPException(429, "Gerade laufen zu viele Analysen – bitte gleich nochmal.")
    login = (by or "-").split("@")[0]
    if login != "-" and sum(1 for j in jobs.values() if j["state"] in ("queued", "running")
                            and j.get("by", "-").split("@")[0] == login) >= MAX_JOBS_PER_LOGIN:
        raise HTTPException(429, f"Du hast schon {MAX_JOBS_PER_LOGIN} Analysen laufen – bitte warten, bis eine fertig ist.")


def _from_cache(raw, filename, options, by):
    """0.46.0: schon gerechnetes Ergebnis -> (job_id, None) bzw. (None, key) / (None, None) ohne Treffer.
    Fehler im Zwischenspeicher verhindern nie die Analyse (Review 12.11.2026)."""
    try:
        ck = resultcache.key(raw, options or {}, _cache_stamp())
        hit = resultcache.load(ck)
    except Exception:
        log.exception("Zwischenspeicher nicht lesbar")
        return None, None
    if hit is None:
        return None, ck
    res = {**hit, "file": filename}
    if res.pop("_name_from_file", False):
        res["name"] = filename.rsplit(".", 1)[0]
    with jobs_lock:
        # derselbe Login, dieselbe Datei: vorhandenen Job zurueckgeben statt eine weitere Kopie im Speicher
        same = next((k for k, j in jobs.items() if j.get("from_cache") == ck and j.get("by") == by
                     and (j.get("result") or {}).get("file") == filename), None)
        if same:
            return same, None
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"state": "done", "progress": 100, "msg": "fertig", "t": time.time(), "by": by,
                        "result": res, "from_cache": ck}
    km = (hit.get("stats") or {}).get("km") or sum((r.get("stats") or {}).get("km", 0) for r in hit.get("stages", []))
    events.log("analyse", (by or "-").split("@")[0], "rig" if (options or {}).get("rig") else "ride",
               name=filename, km=round(km or 0, 1), sec=0, cache=True)
    log.info("Analyse %s (%s) aus dem Zwischenspeicher", filename, by)
    return job_id, None


def _submit(raw, filename, options=None, by="-", cache=True):
    _cleanup()
    with jobs_lock:   # Grenzen zuerst – wer drueber ist, kostet kein GPX-Lesen
        _check_limits(by)
    ck = None
    if cache:
        hit_job, ck = _from_cache(raw, filename, options, by)
        if hit_job:
            return {"job": hit_job}
    try:
        pts, _ = analyze.parse_gpx(raw)   # einmal lesen, fuer Live-Pruefung und Laenge
    except analyze.AnalyzeError:
        pts = None
    live = analyze.needs_live(raw, pts) if pts is not None else False
    km = jobqueue.route_km(raw, pts) if pts is not None else 0.0
    lane = jobqueue.lane_for(km, live, SHORT_KM, SHORT_JOBS)
    with jobs_lock:
        _check_limits(by)
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"state": "queued", "progress": 0, "msg": "wartet", "t": time.time(), "by": by, "live": live,
                        "lane": lane, "seq": next(_seq), "km": km, "est": rate.est(km), "cache_key": ck}
    POOLS[lane]().submit(_run, job_id, raw, filename, options)
    return {"job": job_id}


def _asset_token(folder, files):
    """Kurzer Hash ueber Skripte/Stile: aendert sich mit jedem Update -> Browser
    (v. a. Firefox) laedt garantiert die neue Fassung statt einer gecachten."""
    import hashlib
    h = hashlib.sha1(usedforsecurity=False)
    for f in files:
        try:
            with open(os.path.join(HERE, folder, f), "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
    return h.hexdigest()[:10]


def _index(folder, files):
    shared = ["theme.js", "plantext.js", "planner.js", "layout.js", "weather.js", "v2.css", "quality.js",
              "autobahn.js", "usermenu.js", "dock.js", "dock.css", "queuetext.js"]   # static/, beide Seiten (Test: test_asset_tokens)
    tok = _asset_token(folder, files) + _asset_token("static", shared)
    with open(os.path.join(HERE, folder, "index.html"), encoding="utf-8") as f:
        html = f.read()
    for a in files + shared:
        for prefix in ("/static/", "/static-ride/", "/static-rig/"):
            html = html.replace(f'{prefix}{a}"', f'{prefix}{a}?v={tok}"')
    return html


INDEX_HTML = _index("static-ride", ["app.js", "style.css", "compare.js"])
RIG_HTML = _index("static-rig", ["rig.js", "rig.css", "fuelplan.js", "importplan.js", "lez.js", "toll.js", "shops.js", "listsearch.js",
                                 "camps.js", "camps-filter.js", "detailcard.js", "speedtext.js", "teilen.js", "aufenthalt.js", "unterwegs.js", "navistatus.js"])


@core.get("/")
def index(request: Request):
    return HTMLResponse(RIG_HTML if is_rig(request) else INDEX_HTML, headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- Release-Seite
TAGS = {"neu": "Neu", "besser": "Besser", "fix": "Behoben"}
APPS = {"ride": "Ride", "rig": "Rig"}
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


_APP_DIRS = ("static-ride/", "static-rig/")   # App-eigene Skripte; alles andere (auch "vendor/…") liegt in static/


def _page(rig, badge, intro, body, scripts=()):
    """Unterseite (Aenderungen, Funktionen) im Look der jeweiligen App: Logo, Theme, Footer."""
    from html import escape as e
    folder, css, name, claim = (("static-rig", "rig.css", "RigAnalyzer", "Wissen, ob's passt. Bevor es eng wird.") if rig
                                else ("static-ride", "style.css", "RideAnalyzer", "Der Blick macht die Tour."))
    # scripts: Dateiname in static/ oder "static-rig/datei.js" (App-eigene Skripte)
    own = [s.split("/", 1) for s in scripts if s.startswith(_APP_DIRS)]
    tok = (_asset_token(folder, [css]) + _asset_token("static", ["theme.js", "releases.js", "pages.css", "v2.css", "usermenu.js",
                                                                 *[s for s in scripts if not s.startswith(_APP_DIRS)]])
           + "".join(_asset_token(d, [f]) for d, f in own))
    with open(os.path.join(HERE, folder, "index.html"), encoding="utf-8") as f:
        idx = f.read()
    logo = idx[idx.index('<svg class="logo"'):idx.index("</svg>", idx.index('<svg class="logo"')) + 6]
    return f'''<!doctype html>
<html lang="de" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} – {e(badge)}</title>
<link rel="icon" href="/{folder}/icon.svg" type="image/svg+xml">
<script src="/static/theme.js?v={tok}"></script>
<link rel="stylesheet" href="/{folder}/{css}?v={tok}">
<link rel="stylesheet" href="/static/pages.css?v={tok}">
<link rel="stylesheet" href="/static/v2.css?v={tok}">
</head>
<body>
<header class="top">
  <a class="brand home" href="/">{logo}
    <div><h1>{name} <span class="badge">{e(badge)}</span></h1><p class="claim">{e(claim)}</p></div></a>
  <a class="theme-toggle logout-btn" href="/logout" title="Abmelden">⎋ <span class="lbl">Abmelden</span></a>
  <button type="button" class="theme-toggle" id="theme-toggle" aria-label="Design umschalten">
    <span class="ico" aria-hidden="true">☀️</span><span class="lbl">Hell</span>
  </button>
</header>
<main class="rel-wrap">
  <p class="rel-intro">{intro}</p>
  {body}
</main>
<footer class="foot">{name} v{app_version(rig)} · Stand {STAMP} · <a href="/">Analyse</a> · <a href="/features.html">Funktionen</a> ·
  <a href="/releases.html">Änderungen</a> · <a href="/faq.html">Häufige Fragen</a> · <a href="/tech.html">Technik</a> · <a href="/datenschutz">Datenschutz</a> · <a href="/impressum">Impressum</a> ·
  {'<a href="/app.html">Android-App</a> · ' if rig else ""}Kartendaten © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap-Mitwirkende</a> (ODbL)</footer>
<script src="/static/releases.js?v={tok}"></script>
<script src="/static/usermenu.js?v={tok}"></script>
{"".join(f'<script src="/{s if s.startswith(_APP_DIRS) else "static/" + s}?v={tok}"></script>' for s in scripts)}
</body>
</html>'''  # noqa: E501 – HTML-Vorlage, ein Umbruch wuerde die Ausgabe aendern


def app_version(rig):
    """Versionsnummer je App (seit 0.17.2): die neueste Aenderung, die diese App betrifft (eigene oder „beide“)."""
    mine = ("rig", "beide") if rig else ("ride", "beide")
    return next((r["version"] for r in _releases() if r.get("app") in mine), VERSION)


def _releases():
    with open(os.path.join(HERE, "releases.json"), encoding="utf-8") as f:
        return [r for r in json.load(f) if not r.get("pending")]   # pending: gesammelt fuers naechste Release


REL_BLOCK = 5


def _releases_html(rig):
    """Oeffentlich nur die Meilensteine mit einem Satz (public/public_text); alles Weitere im Admin-Bereich."""
    from html import escape as e
    out = []
    mine = ("rig", "beide") if rig else ("ride", "beide")
    app = "rig" if rig else "ride"
    for r in _releases():
        if not r.get("public") or r["app"] not in mine:
            continue
        y, m = (int(x) for x in r["date"].split("-")[:2])   # nur Monat + Jahr, nicht der genaue Tag
        ms = f'<span class="ms">{e(r["milestone"])}</span>' if r.get("milestone") else ""
        # eigener Text je App, wenn ein Eintrag beide betrifft (Ride/Rig strikt getrennt)
        text = r.get(f"public_text_{app}") or r["public_text"]
        out.append(f'''<article class="rel card major" id="v{e(r['version'])}">
  <header><a class="ver" href="#v{e(r['version'])}">v{e(r['version'])}</a>{ms}
    <time datetime="{y:04d}-{m:02d}">{MONTHS[m - 1]} {y}</time></header>
  <h2>{e(r.get(f"title_{app}") or r['title'])}</h2>
  <p>{e(text)}</p>
</article>''')
    # alle oeffentlichen Eintraege, die 5 neuesten offen, danach je 5 hinter „Mehr anzeigen“ (verschachtelt,
    # ohne JavaScript; ein Link auf #v… klappt im Browser den passenden Block selbst auf)
    rest = ""
    for k in range(len(out) - (len(out) - 1) % REL_BLOCK - 1, REL_BLOCK - 1, -REL_BLOCK):
        n = len(out) - k
        rest = (f'<details class="rel-more"><summary>Mehr anzeigen ({min(REL_BLOCK, n)} von {n} älteren)</summary>'
                + "".join(out[k:k + REL_BLOCK]) + rest + "</details>")
    out = out[:REL_BLOCK] + ([rest] if rest else [])
    intro = "Die wichtigsten Schritte seit dem ersten Prototyp, das Neueste oben. <a href=\"/\">← zurück zur Analyse</a>"
    return _page(rig, "Änderungen", intro, "".join(out))


def _coverage_card(rig):
    """Kartendaten und Grenzen (seit 0.31.2 hier statt auf der Startseite): Laender mit lokalen Daten, Planungsgebiet."""
    from html import escape as e
    db = osmdb.get()
    cs = regions.by_country(db.regions) if db else []
    n = len(db.regions) if db else 0
    lis = "".join(f"<li><b>{e(c['name'])}</b>{(' <span>(' + e(', '.join(c['regions'])) + ')</span>') if c['regions'] else ''}</li>"
                  for c in cs)
    local = (f"<li><b>Lokale Kartendaten</b> <span>für {len(cs)} Länder in {n} Regionen (schnell) – auf der Karte "
             "eingefärbt nach Alter der Daten, monatlich aktualisiert. Liegt eine Route woanders, holt die Seite die Daten "
             "live bei OpenStreetMap (langsamer) und bietet an, die Region dauerhaft aufzunehmen.</span></li>") if cs else \
        "<li><b>Kartendaten</b> <span>werden live bei OpenStreetMap geholt – das dauert je nach Länge ein bis mehrere Minuten.</span></li>"
    limits = (f"<li><b>Grenzen</b> <span>GPX bis {analyze.MAX_KM:g} km je Strecke bzw. Etappe, Datei bis {MAX_BYTES // 1024 // 1024} MB.</span></li>")
    plan = f"<li><b>Routenplaner</b> <span>plant im Gebiet {e(planner.AREA)}.</span></li>" if rig else ""
    lst = f'<details class="cov-list"><summary>Welche Länder?</summary><ul>{lis}</ul></details>' if cs else ""
    return (f'<article class="feat-sec card" id="karten"><h2><span aria-hidden="true">🗺️</span> Kartendaten &amp; Grenzen</h2>'
            f'<ul>{local}{limits}{plan}</ul>{lst}</article>')


def _features_html(rig):
    """Was die App alles kann (features.json): eigene Abschnitte je App + gemeinsame."""
    from html import escape as e
    with open(os.path.join(HERE, "features.json"), encoding="utf-8") as f:
        ft = json.load(f)
    me = ft["rig" if rig else "ride"]
    secs = me["sections"] + ft["common"]
    n = sum(len(sec["items"]) for sec in secs)
    out = []
    for sec in secs:
        items = "".join(f"<li><b>{e(t)}</b> <span>{e(x.replace('{{plan_area}}', planner.AREA))}</span></li>" for t, x in sec["items"])
        out.append(f'''<article class="feat-sec card"><h2><span aria-hidden="true">{sec['icon']}</span> {e(sec['title'])}</h2><ul>{items}</ul></article>''')
    intro = (f"{e(me['intro'])} <b>{n} Funktionen</b> auf einen Blick. "
             "<a href=\"/\">← zurück zur Analyse</a>")   # ohne Link zur Schwester-App
    out.append(_coverage_card(rig))
    if me.get("planned"):
        pl = me["planned"]
        items = "".join(f"<li><b>{e(t)}</b> <span>{e(x)}</span></li>" for t, x in pl["items"])
        out.insert(0, f'''<article class="feat-sec card planned"><h2><span aria-hidden="true">{pl['icon']}</span> '''
                      f'''{e(pl['title'])}</h2><ul>{items}</ul></article>''')
    return _page(rig, "Funktionen", intro, f'<div class="feat-grid">{"".join(out)}</div>')


def _faq_html(rig):
    """Häufige Fragen (0.37.0) aus faq/ (faq.py): nur die Dateien dieser App plus „beide“."""
    from html import escape as e, unescape
    secs = faq.sections("rig" if rig else "ride", "RigAnalyzer" if rig else "RideAnalyzer")

    def t(x):   # TOC-Namen kommen von Markdown schon escaped – nie doppelt, nie roh
        return e(unescape(x), quote=False)

    toc = []
    for s in secs:
        qs = "".join(f'<li><a href="#{e(q)}">{t(n)}</a></li>' for q, n in s["questions"])
        toc.append(f'<li><a href="#{e(s["id"])}">{t(s["title"])}</a>' + (f"<ul>{qs}</ul>" if qs else "") + "</li>")
    toc = "".join(toc)
    body = (f'<nav class="card faq-toc" aria-label="Inhalt"><h2>Inhalt</h2><ul>{toc}</ul></nav>'
            + "".join(f'<article class="card faq-sec">{s["html"]}</article>' for s in secs)) if secs else \
        '<article class="card faq-sec"><p>Die Fragen werden gerade neu sortiert – schaut bitte später noch einmal vorbei.</p></article>'
    intro = ("Antworten auf die Fragen, die immer wieder kommen. Nicht dabei? Schreibt uns über "
             "<b>💬 Rückmeldung</b> im Menü oben rechts. <a href=\"/\">← zurück zur Analyse</a>")
    return _page(rig, "Häufige Fragen", intro, f'<div class="faq">{body}</div>')


@core.get("/faq.html")
@core.get("/faq")
def faq_page(request: Request):
    return HTMLResponse(_faq_html(is_rig(request)), headers={"Cache-Control": "no-cache"})


@core.get("/releases.html")
@core.get("/releases")
def releases(request: Request):
    return HTMLResponse(_releases_html(is_rig(request)), headers={"Cache-Control": "no-cache"})


_live_cache = {"t": 0, "v": {}}


def _live_values():
    """Versionen & Zahlen fuer die Technikseite – aus dem laufenden System statt von Hand gepflegt.
    Bibliotheken direkt, Valhalla ueber /status, Hosts aus data/host-info.json (schreibt Ansible)."""
    import sqlite3 as _sq
    from importlib.metadata import version as _ver
    if time.time() - _live_cache["t"] < 60:
        return _live_cache["v"]
    v = {"app": VERSION, "python": sys.version.split()[0], "sqlite": _sq.sqlite_version, "plan_area": planner.AREA}
    for key, pkg in (("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("shapely", "shapely"), ("numpy", "numpy"),
                     ("osmium", "osmium"), ("httpx", "httpx"), ("tifffile", "tifffile")):
        try:
            v[key] = _ver(pkg)
        except Exception:  # noqa: BLE001 – Versionsanzeige ist Beiwerk
            v[key] = "?"
    try:
        with open(os.path.join(HERE, "static", "vendor", "leaflet", "leaflet.js"), encoding="utf-8") as f:
            import re as _re
            v["leaflet"] = _re.search(r"Leaflet (\d+\.\d+\.\d+)", f.read(400)).group(1)
    except Exception:  # noqa: BLE001 – Versionsanzeige ist Beiwerk
        v["leaflet"] = "?"
    try:
        import httpx as _hx
        v["valhalla"] = _hx.get(planner.VALHALLA_URL + "/status", timeout=3).json().get("version", "?")
    except Exception:  # noqa: BLE001 – Versionsanzeige ist Beiwerk, Valhalla evtl. weg
        v["valhalla"] = "(gerade nicht erreichbar)"
    db = osmdb.get()
    if db:
        v["regions"] = str(len(db.regions))
        v["countries"] = str(len(regions.by_country(db.regions)))
        v["osm_date"] = ".".join(reversed((db.meta.get("built") or "?")[:10].split("-")))
    osm_dir = os.environ.get("OSM_DIR", "/data/osm")
    try:
        gb = sum(os.path.getsize(os.path.join(osm_dir, f)) for f in os.listdir(osm_dir) if f.endswith(".sqlite")) / 1e9
        v["osm_gb"] = f"{gb:.0f}"
    except OSError:
        pass
    dem_dir = os.environ.get("DEM_DIR", "/data/dem")
    try:
        tifs = [f for f in os.listdir(dem_dir) if f.endswith(".tif")]
        v["dem_tiles"] = str(len(tifs))
        v["dem_mb"] = f"{sum(os.path.getsize(os.path.join(dem_dir, f)) for f in tifs) / 1e6:.0f}"
    except OSError:
        pass
    try:
        with open(os.path.join(os.environ.get("DATA_DIR", "/data"), "host-info.json"), encoding="utf-8") as f:
            hi = json.load(f)
        for host in ("server", "proxy"):
            lines = hi.get(host) or []
            for k, i in (("model", 0), ("os", 1), ("kernel", 2), ("docker", 3), ("ram", 4), ("cpus", 5)):
                if len(lines) > i:
                    val = lines[i].strip()
                    if k == "model":
                        val = val.split(" Rev ")[0]          # "Raspberry Pi 4 Model B Rev 1.1"
                    elif k == "ram":
                        val = val.replace(".", ",")
                    v[f"{host}_{k}"] = val
        b = hi.get("proxy") or []
        if len(b) > 6:
            v["traefik"] = b[6].split(":")[-1]
    except (OSError, ValueError):
        pass
    _live_cache.update(t=time.time(), v=v)
    return v


def _fill_live(html):
    """{{name}} -> Wert mit Live-Kennzeichen (Sternchen); Unbekanntes bleibt sichtbar als „?“."""
    import re as _re
    from html import escape as _e
    vals = _live_values()

    def rep(m):
        val = vals.get(m.group(1))
        if val is None:
            return "?"
        return f'<span class="live" title="Live-Wert aus dem laufenden System">{_e(str(val))}<sup>✱</sup></span>'
    return _re.sub(r"\{\{(\w+)\}\}", rep, html)


def _only_app(html, rig):
    """<!--ride-->…<!--/ride--> bzw. <!--rig-->…<!--/rig--> der jeweils anderen App entfernen (seit 0.15.2/0.17.2)."""
    import re
    other = "ride" if rig else "rig"
    return re.sub(rf"<!--{other}-->.*?<!--/{other}-->", "", html, flags=re.S)


def _tech_html(rig):
    """Technik & Hardware (tech.html, fuer beide Domains gleich – es ist dieselbe Anlage)."""
    with open(os.path.join(HERE, "tech.html"), encoding="utf-8") as f:
        body = _fill_live(f.read().replace("{{app}}", app_version(rig)))
    body = _only_app(body, rig)
    intro = (f"Worauf der {'RigAnalyzer' if rig else 'RideAnalyzer'} läuft und wie er arbeitet: Hardware, Software, Daten und der Weg einer Anfrage. "
             "<a href=\"/\">← zurück zur Analyse</a>")
    return _page(rig, "Technik", intro, body)


def _app_html(rig):
    """Anleitung: Navi-App aufs Android-Handy (Groesse/Stand der APK automatisch)."""
    with open(os.path.join(HERE, "app-install.html"), encoding="utf-8") as f:
        body = f.read()
    apk = os.path.join(os.environ.get("DATA_DIR", "/data"), "app", "rignavi.apk")
    try:
        st = os.stat(apk)
        size, date = f"{st.st_size / 1e6:.0f}", time.strftime("%d.%m.%Y %H:%M", time.localtime(st.st_mtime))
    except OSError:
        size, date = "?", "noch nicht bereitgestellt"
    body = body.replace("{apk_size}", size).replace("{apk_date}", date).replace("{plan_area}", planner.AREA)
    intro = ("So kommt RigNavi aufs Android-Handy, in sechs Schritten. "
             "<a href=\"/\">← zurück zur Analyse</a>")
    return _page(rig, "Android-App", intro, body)


@core.get("/app.html")
@core.get("/app")
def app_install(request: Request):
    if not is_rig(request):   # RigNavi gehoert zum RigAnalyzer – vom RideAnalyzer aus gibt es die Seite nicht
        raise HTTPException(404, "Nicht gefunden.")
    return HTMLResponse(_app_html(is_rig(request)), headers={"Cache-Control": "no-cache"})


@core.post("/api/weather")
async def api_weather(request: Request):
    """Wetter je Etappe + Warnungen (seit v0.13) – Webseite und RigNavi, Login noetig."""
    import weather
    try:
        body = await request.json()
        return await asyncio.to_thread(weather.route_weather, body)
    except (ValueError, TypeError, KeyError, IndexError) as e:
        raise HTTPException(422, f"Ungültige Anfrage: {e}") from None


# ---------------------------------------------------------------- Eigene Platz-Bewertungen


@core.get("/api/weather/radar")
async def api_weather_radar():
    """Letztes Regenradar-Bild (RainViewer), Kachel-URL fuer Leaflet/MapLibre (seit v0.13)."""
    import weather
    try:
        return await asyncio.to_thread(weather.radar_latest)
    except Exception as e:   # noqa: BLE001 – Zusatz – Ausfall nur melden
        raise HTTPException(424, f"Regenradar gerade nicht abrufbar ({type(e).__name__})") from None


@core.get("/tech.html")
@core.get("/tech")
def tech(request: Request):
    return HTMLResponse(_tech_html(is_rig(request)), headers={"Cache-Control": "no-cache"})


@core.get("/features.html")
@core.get("/features")
def features(request: Request):
    return HTMLResponse(_features_html(is_rig(request)), headers={"Cache-Control": "no-cache"})


@core.post("/api/analyze")
async def api_analyze(file: UploadFile, request: Request):
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, f"Datei zu groß (max. {MAX_BYTES // 1024 // 1024} MB).")
    if not raw.strip():
        raise HTTPException(400, "Leere Datei.")
    name = os.path.basename(file.filename or "route.gpx")[:120]
    # GPX-Lesen und Zwischenspeicher nicht im Event-Loop
    return await run_in_threadpool(_submit, raw, name, {"rig": is_rig(request)}, who(request))


@core.post("/api/plan")
async def api_plan(request: Request):
    """Route mit dem eigenen Valhalla planen -> GPX (die Seite schickt sie danach in die Analyse)."""
    body = await request.json()
    rig = is_rig(request)
    t0 = time.monotonic()
    try:
        res = await asyncio.to_thread(planner.plan, body.get("points"), "rig" if rig else "ride",
                                      body.get("mode", "fast"), body.get("vehicle"), body.get("name"),
                                      body.get("avoid_zones") if rig else None, body.get("nights") if rig else None,
                                      body.get("avoid") if rig else None)
    except planner.PlanError as e:
        raise HTTPException(422, str(e)) from None
    access.info("%s plant %d Punkte (%s, %s) -> %s km", who(request), len(body.get("points") or []),
                res["costing"], body.get("mode"), res["km"])
    events.log("planung", request.state.user, "rig" if rig else "ride", km=res["km"], mode=body.get("mode"))
    usage.add(request.state.user, "rig" if rig else "ride", plans=1, plan_sec=round(time.monotonic() - t0, 1))
    return res


@core.post("/api/plan/variants")
async def api_plan_variants(request: Request):
    """Alternativrouten (0.41.0, Ticket #21, nur RigAnalyzer): bis zu 3 Varianten zur Auswahl, siehe planner.variants."""
    if not is_rig(request):
        raise HTTPException(404, "Nicht gefunden.")
    body = await request.json()
    t0 = time.monotonic()
    try:
        res = await asyncio.to_thread(planner.variants, body.get("points"), body.get("vehicle"), body.get("mode", "fast"),
                                      body.get("avoid_zones"), body.get("avoid"))
    except planner.PlanError as e:
        raise HTTPException(422, str(e)) from None
    access.info("%s Varianten fuer %d Punkte -> %d", who(request), len(body.get("points") or []), len(res))
    usage.add(request.state.user, "rig", plans=1, plan_sec=round(time.monotonic() - t0, 1))
    return {"variants": res}


@core.post("/api/nav/route")
async def api_nav_route(request: Request):
    """Routing fuer die Navi-App (Ferrostar): Valhalla-Anfrage, Fahrzeug aus den URL-Parametern."""
    q = request.query_params
    veh = {"height": q.get("h"), "width": q.get("w"), "length": q.get("l"), "weight": q.get("t"),
           "axle": q.get("ax"), "vmax": q.get("vmax"), "trailer": q.get("trailer") in ("1", "true")}
    try:
        body = await request.json()
        zones = [z for z in (q.get("lez") or "").split(",") if z] if is_rig(request) else None
        # Maut/Faehren meiden: notoll=1 / noferry=1
        avoid = {"toll": q.get("notoll") == "1", "ferry": q.get("noferry") == "1"}
        status, content, costing, lez_note = await asyncio.to_thread(planner.nav_route, body, veh, q.get("nomw") == "1", zones, avoid)
    except planner.PlanError as e:
        raise HTTPException(422, str(e)) from None
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    access.info("%s Navi-Route (%s, %s Punkte) -> %s", who(request), costing, len(body.get("locations") or []), status)
    if status == 200:
        usage.add(request.state.user, "rig", routes=1)
    hdr = {"X-RA-LEZ-Note": quote(lez_note)} if lez_note else None   # Umlaute -> %-kodiert
    return Response(content=content, status_code=status, media_type="application/json", headers=hdr)


# ---------------------------------------------------------------- RigNavi-Reisen + Offline-Pakete


# ---------------------------------------------------------------- Meine Touren


# ---------------------------------------------------------------- Analyse zur Tour speichern


# ---------------------------------------------------------------- Touren teilen


GEO_PER_MIN = 20   # Ortssuchen je Login und Minute (Nominatim erlaubt ohnehin nur 1/s fuer alle zusammen)
_geo_hits: dict = {}
_geo_hits_lock = threading.Lock()


def _geo_throttled(user):
    now = time.time()
    with _geo_hits_lock:
        hits = [x for x in _geo_hits.get(user, []) if now - x < 60]
        if len(hits) >= GEO_PER_MIN:
            _geo_hits[user] = hits
            return True
        _geo_hits[user] = hits + [now]
        if len(_geo_hits) > 500:
            for k in [k for k, v in _geo_hits.items() if not v or now - v[-1] >= 60]:
                del _geo_hits[k]
        return False


@core.get("/api/geocode")
async def api_geocode(request: Request, q: str = ""):
    if _geo_throttled(request.state.user or _client_ip(request)):
        raise HTTPException(429, "Sehr viele Suchen in kurzer Zeit – bitte einen Moment warten.")
    try:
        return await asyncio.to_thread(planner.geocode, q)
    except planner.GeoBusy as e:
        raise HTTPException(429, str(e)) from None
    except planner.PlanError as e:
        raise HTTPException(424, str(e)) from None   # nicht 503: das faengt der Reverse-Proxy als Wartungsseite ab


@core.get("/api/luftbild/{z}/{x}/{y}")
def api_luftbild(z: int, x: int, y: int, request: Request):
    """Luftbild-Kachel ueber den eigenen Server (0.52.1) – der Browser laedt nichts mehr direkt bei Esri."""
    try:
        hit = luftbild.tile(z, x, y, request.state.user)
    except ValueError:
        raise HTTPException(404, "Keine Kachel.") from None
    except luftbild.TooMany:
        raise HTTPException(429, "Sehr viele Kacheln in kurzer Zeit – bitte einen Moment warten.") from None
    if not hit:
        raise HTTPException(404, "Luftbild gerade nicht verfügbar.")
    return Response(hit[0], media_type=hit[1])


@core.get("/api/place")
async def api_place(lat: float, lon: float):
    """Namensvorschlag fuer einen per Klick gesetzten Planer-Punkt (0.47.0, Ticket #9): „Wetzlar“ / „bei Gießen“ aus
    den lokalen Kartendaten, ohne fremden Dienst. "" = nichts Passendes (dann bleibt die Koordinate)."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(422, "Koordinate ungültig")
    db = osmdb.get()
    if db is None:
        return {"name": ""}
    try:
        return {"name": await asyncio.to_thread(db.place_name, lat, lon)}
    except Exception:   # ein Vorschlag darf nie das Setzen eines Punkts stoeren
        log.warning("Namensvorschlag fuer %.4f,%.4f fehlgeschlagen", lat, lon, exc_info=True)
        return {"name": ""}


def _split_long(raw):
    """Ein Track/eine Route ueber MAX_KM (nur Rig, 0.47.0, Ticket #5) -> (Etappen, Name, Info) – sonst ([], "", None).
    Geschnitten wird an Orten aus den lokalen Kartendaten; ohne Daten nach km."""
    import etappen
    pts, name = analyze.parse_gpx(raw)
    if etappen.track_km(pts)[-1] <= analyze.MAX_KM:
        return [], "", None
    db = osmdb.get()
    stages, info = etappen.split_long(pts, analyze.MAX_KM, places=db.places_near if db else None)
    log.info("Lange Strecke %.0f km automatisch in %d Etappen geteilt", info["km"], info["parts"])
    return stages, name or "", info


PUBLIC_URL = {True: os.environ.get("PUBLIC_RIG", "https://riganalyzer.petrick.top"),
              False: os.environ.get("PUBLIC_RIDE", "https://rideanalyzer.petrick.top")}


def _card_page(rig, title, inner, wide=False):
    """Kleine Seite im Look der Anmeldeseite (Passwort vergessen/neu); wide fuer lange Texte (Datenschutz, 0.52.0)."""
    page = _login_html(rig)
    a, b = page.index('<main class="lg-card card">'), page.index("</main>")
    head = page[:a].replace("– Anmelden</title>", f"– {title}</title>")
    brand = page[page.index('<div class="lg-brand">', a):page.index("</div></div>", a) + 12]
    cls = "lg-card lg-wide card" if wide else "lg-card card"
    return f'{head}<main class="{cls}">\n  {brand}\n  {inner}\n{page[b:]}'


def _login_html(rig, error="", bye=False, nxt="", user=""):
    """Schlichte, eigene Anmeldeseite im Look der jeweiligen App."""
    from html import escape as e
    folder, css, name, claim = (("static-rig", "rig.css", "RigAnalyzer", "Wissen, ob's passt. Bevor es eng wird.") if rig
                                else ("static-ride", "style.css", "RideAnalyzer", "Der Blick macht die Tour."))
    with open(os.path.join(HERE, folder, "index.html"), encoding="utf-8") as f:
        idx = f.read()
    logo = idx[idx.index('<svg class="logo"'):idx.index("</svg>", idx.index('<svg class="logo"')) + 6]
    tok = _asset_token(folder, [css]) + _asset_token("static", ["theme.js", "pages.css", "v2.css", "pwtoggle.js"])
    msg = (f'<p class="lg-err">{e(error)}</p>' if error else
           '<p class="lg-ok">Du bist abgemeldet. Gute Fahrt! 👋</p>' if bye else "")
    return f'''<!doctype html>
<html lang="de" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} – Anmelden</title>
<link rel="icon" href="/{folder}/icon.svg" type="image/svg+xml">
<script src="/static/theme.js?v={tok}"></script>
<link rel="stylesheet" href="/{folder}/{css}?v={tok}">
<link rel="stylesheet" href="/static/pages.css?v={tok}">
<link rel="stylesheet" href="/static/v2.css?v={tok}">
<script src="/static/pwtoggle.js?v={tok}" defer></script>
</head>
<body class="login-body">
<main class="lg-card card">
  <div class="lg-brand">{logo}<div><h1>{name} <span class="badge">Alpha</span></h1><p class="claim">{e(claim)}</p></div></div>
  {msg}
  <form method="post" action="/login" class="lg-form">
    <input type="hidden" name="next" value="{e(nxt)}">
    <label>Benutzer<input name="user" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required value="{e(user)}" {"" if user else "autofocus"}></label>
    <label>Passwort<input name="password" type="password" autocomplete="current-password" required {"autofocus" if user else ""}></label>
    <label class="lg-chk"><input type="checkbox" name="remember" value="1" checked> Angemeldet bleiben (30 Tage)</label>
    <button type="submit" class="primary">Anmelden</button>
  </form>
  <p class="lg-foot"><a href="/passwort-vergessen">Passwort vergessen?</a> · Noch keinen Zugang? <a href="/registrieren">Registrieren</a></p>
  <p class="lg-foot"><a href="/info.html">Was ist {name}?</a></p>
</main>
<p class="lg-legal"><a href="/datenschutz">Datenschutz</a> · <a href="/impressum">Impressum</a></p>
</body>
</html>'''  # noqa: E501 – HTML-Vorlage, ein Umbruch wuerde die Ausgabe aendern


def _safe_next(nxt):
    return nxt if isinstance(nxt, str) and nxt.startswith("/") and not nxt.startswith("//") else "/"


@core.get("/login")
def login_page(request: Request, next: str = "", bye: int = 0):
    if request.state.user and not bye:
        return RedirectResponse(_safe_next(next), status_code=303)
    return HTMLResponse(_login_html(is_rig(request), bye=bool(bye), nxt=next), headers={"Cache-Control": "no-store"})


@core.post("/login")
async def login_post(request: Request):
    form = await request.form()
    user, pw = str(form.get("user", "")).strip().lower(), str(form.get("password", ""))
    nxt, remember = _safe_next(str(form.get("next", ""))), form.get("remember") == "1"
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "?")
    rig = is_rig(request)
    if auth.throttled(ip):
        access.info("-@%s Login gesperrt (zu viele Fehlversuche) fuer %s", ip, user)
        return HTMLResponse(_login_html(rig, "Zu viele Fehlversuche – bitte in 15 Minuten nochmal.", nxt=nxt, user=user),
                            status_code=429)
    if not await asyncio.to_thread(auth.check, user, pw, auth_app(request)):
        if await asyncio.to_thread(auth.pending_ok, user, pw):
            auth.release(ip)
            access.info("-@%s Login %s wartet noch auf Freischaltung", ip, user)
            return HTMLResponse(_login_html(rig, "Dein Zugang ist angelegt, wartet aber noch auf die Freischaltung. "
                                                 "Du bekommst eine Mail, sobald es losgehen kann.", nxt=nxt, user=user),
                                status_code=403)
        auth.failed(ip)
        if user in auth.users():
            activity.record_login(user, ip, "Browser", False)
        events.log("login", "-", auth_app(request), ok=False, ip=ip, device="Browser", tried=str(user)[:40])
        loginwatch.login(str(user)[:40], auth_app(request), ip, ok=False)
        access.info("-@%s Login fehlgeschlagen fuer %s", ip, user)
        return HTMLResponse(_login_html(rig, "Benutzer oder Passwort stimmt nicht.", nxt=nxt, user=user), status_code=401)
    auth.release(ip)
    await asyncio.to_thread(auth.touch_login, user)
    activity.record_login(user, ip, "Browser", True)
    events.log("login", user, auth_app(request), ok=True, ip=ip, device="Browser")
    loginwatch.login(user, auth_app(request), ip, detail="2FA folgt" if auth.is_admin(user) and twofa.enabled(user) else "")
    if auth.is_admin(user) and twofa.enabled(user):
        # Admin mit 2FA: erst der Code, dann die Sitzung (Zwischen-Cookie 5 min)
        access.info("%s@%s Passwort ok, 2FA-Code ausstehend", user, ip)
        resp = RedirectResponse("/zweiter-faktor" + ("?next=" + quote(nxt) if nxt != "/" else ""), status_code=303)
        resp.set_cookie(twofa.PEND_COOKIE, twofa.make_pending(user, auth_app(request), remember), max_age=twofa.PEND_TTL,
                        httponly=True, secure=True, samesite="strict", path="/")
        return resp
    access.info("%s@%s angemeldet%s", user, ip, " (30 Tage)" if remember else "")
    resp = RedirectResponse(nxt, status_code=303)
    _set_session(resp, user, auth_app(request), remember)
    return resp


@core.get("/logout")
@core.post("/logout")
def logout(request: Request):
    access.info("%s abgemeldet", who(request))
    try:   # Sitzung auch serverseitig beenden – ein kopiertes Cookie ist danach wertlos
        auth.revoke(request.cookies.get(auth.COOKIE, ""))
    except OSError:
        log.exception("Abmelden: Sperrliste nicht schreibbar")
    resp = RedirectResponse("/login?bye=1", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    # auch 2FA-Nachweis und CSRF-Token wegraeumen (allein wertlos, aber sauberer)
    resp.delete_cookie(twofa.MFA_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    resp.delete_cookie(auth.CSRF_COOKIE, path="/", secure=True, httponly=False, samesite="strict")
    return resp


# ---------------------------------------------------------------- Passwort vergessen + Mein Konto
# Kurzvorstellung fuer Leute ohne Login (info.html, von der Anmeldeseite verlinkt)
_INFO = {
    False: ('<p class="lg-info-lead">RideAnalyzer schaut sich eure Motorradtour <b>vor</b> der Fahrt an: '
            'Macht die Strecke Spaß, oder ist es Ampel, Ortsdurchfahrt und Flickenteppich?</p>'
            '<ul class="lg-info">'
            '<li>🧭 <b>Planen oder prüfen</b> – Route direkt hier planen oder eine fertige Tour (Calimoto, Kurviger, Garmin …) als GPX prüfen lassen.</li>'
            '<li>〰️ <b>Kurven und Fahrfluss</b> – wo es schön schwingt, wo Ampeln, Kreisel und Ortsdurchfahrten den Rhythmus brechen.</li>'
            '<li>🏰 <b>Unterwegs</b> – Belag, Sehenswertes, Cafés und Tankstellen, dazu das Wetter entlang der Strecke.</li>'
            '<li>🏍️ <b>Passt sie zu euch?</b> – Bewertung je Fahrstil: solo zügig, kleine Gruppe, gemütliche Gruppe, Kurvenjagd.</li>'
            '</ul>'
            '<p class="lg-info-lead">Gedacht für die Tourplanung im Freundeskreis – damit die Runde hält, was die Karte verspricht.</p>'
            '<h2 class="lg-info-h">Warum es das gibt</h2>'
            '<p>Angefangen hat alles mit meinem Nachbarn. Wir saßen über einer Tour, die er geplant hatte, und fragten uns: '
            'Wie fährt sich die eigentlich? Viele Kurven oder viel Ortsdurchfahrt? Guter Belag oder Flickenteppich? '
            'Passt das für die ganze Gruppe, und wo machen wir Pause?</p>'
            '<p>Die gängigen Apps finden kurvige Strecken, aber genau diese Fragen beantwortet keine richtig. '
            'Gesucht habe ich lange, zufrieden war ich nie. Also habe ich es selbst versucht. Aus dem Abend mit dem '
            'Nachbarn ist RideAnalyzer geworden: ein zweiter Blick auf die Tour, bevor wir losfahren.</p>'
            '<p>Gebaut habe ich das zusammen mit Claude, einer KI von Anthropic. Wenn hier „wir“ steht, sind wir beide gemeint.</p>'
            '<p>Es ist ein Hobbyprojekt, gerade in der Alpha-Phase. Rückmeldungen sind herzlich willkommen – '
            'nach der Anmeldung über <b>💬 Rückmeldung</b> im Menü oben rechts.</p>'
            '<p class="lg-info-sign"><i>Richy</i></p>'),
    True: ('<p class="lg-info-lead">RigAnalyzer prüft eure Wohnmobil- oder Gespann-Tour <b>vor</b> der Fahrt: '
           'Passt ihr überall durch – und was erwartet euch unterwegs?</p>'
           '<ul class="lg-info">'
           '<li>🚧 <b>Passt es durch?</b> – Höhe, Breite, Länge, Gewicht und Achslast gegen Brücken, Durchfahrten, enge Straßen und Kehren.</li>'
           '<li>⛰️ <b>Steigungen</b> – wo es steil wird, bergauf wie bergab.</li>'
           '<li>⛽ <b>Tankplan</b> – Diesel und AdBlue nach Reichweite, mit echten Preisen in Deutschland und Frankreich.</li>'
           '<li>🌫️ <b>Umweltzonen</b> – Plakette bzw. Crit&#39;Air für euer Fahrzeug, auf Wunsch umfahren.</li>'
           '<li>🏕️ <b>Stehen und Versorgen</b> – Stellplätze, Camping, Ver- und Entsorgung, dazu Maut- und Tempo-Hinweise je Land.</li>'
           '</ul>'
           '<p class="lg-info-lead">„Rig“ ist englisch für das ganze Gefährt – Womo allein oder Zugfahrzeug mit Anhänger. '
           'Dazu gibt es die Android-App <b>RigNavi</b>: Reise ans Handy schicken, nach euren Maßen navigieren – auch ohne Netz.</p>'
           '<h2 class="lg-info-h">Warum es das gibt</h2>'
           '<p>Mit Wohnmobil und Anhänger unterwegs zu sein, heißt vor jeder Reise dieselben Fragen: Passen wir unter der '
           'Brücke durch? Ist die Straße breit genug, ist die Kehre zu eng? Wo tanken wir Diesel und AdBlue? Dürfen wir in '
           'die Umweltzone? Wo können wir über Nacht stehen?</p>'
           '<p>Die Antworten lagen in einem halben Dutzend Apps verteilt, und keine hat ein Gespann wirklich ernst genommen. '
           'Gesucht habe ich lange, zufrieden war ich nie. Also habe ich es selbst versucht. Daraus sind RigAnalyzer und '
           'das Navi RigNavi entstanden: alles für unser Fahrzeug an einem Ort, vor der Fahrt geprüft und unterwegs im Blick. '
           'Und ein bisschen auch, weil mein uraltes iPad von 2017 noch ein super Display hat – und Siedler lief darauf ja auch flüssig. '
           'RigNavi läuft deshalb auch auf dem iPad, wegen Apples Regeln bisher aber nur, wenn ich es selbst aufspiele.</p>'
           '<p>Gebaut habe ich das zusammen mit Claude, einer KI von Anthropic. Wenn hier „wir“ steht, sind wir beide gemeint.</p>'
            '<p>Es ist ein Hobbyprojekt, gerade in der Alpha-Phase. Rückmeldungen sind herzlich willkommen – '
            'nach der Anmeldung über <b>💬 Rückmeldung</b> im Menü oben rechts.</p>'
           '<p class="lg-info-sign"><i>Richy</i></p>'),
}


@core.get("/datenschutz")
@core.get("/datenschutz.html")
def datenschutz_page(request: Request):
    """Datenschutzerklaerung (0.52.0), ohne Anmeldung – vor dem Zugangsantrag muss sie lesbar sein (Art. 13 DSGVO)."""
    rig = is_rig(request)
    return HTMLResponse(_card_page(rig, "Datenschutz", rechtliches.datenschutz(rig), wide=True), headers={"Cache-Control": "no-cache"})


@core.get("/impressum")
@core.get("/impressum.html")
def impressum_page(request: Request):
    rig = is_rig(request)
    return HTMLResponse(_card_page(rig, "Impressum", rechtliches.impressum(rig), wide=True), headers={"Cache-Control": "no-cache"})


@core.get("/info.html")
def info_page(request: Request):
    rig = is_rig(request)
    return HTMLResponse(_card_page(rig, "Worum geht's?", _INFO[rig] + '<p class="lg-foot"><a href="/login">Zur Anmeldung</a> · '
                                   'Noch keinen Zugang? <a href="/registrieren">Registrieren</a></p>'), headers={"Cache-Control": "no-cache"})


_FORGOT_SENT = ('<p class="lg-ok">Falls es den Login gibt und eine Mailadresse hinterlegt ist, ist jetzt eine Mail '
                'mit einem Link unterwegs (1 Stunde gültig).</p><p class="lg-foot"><a href="/login">Zur Anmeldung</a></p>')
_FORGOT_FORM = ('<form method="post" action="/passwort-vergessen" class="lg-form">'
                '<label>Benutzer oder Mailadresse<input name="who" autocomplete="username" autocapitalize="none" required autofocus></label>'
                '<button type="submit" class="primary">Link schicken</button></form>'
                '<p class="lg-foot"><a href="/login">Zurück zur Anmeldung</a></p>')


@core.get("/passwort-vergessen")
def forgot_page(request: Request):
    return HTMLResponse(_card_page(is_rig(request), "Passwort vergessen", _FORGOT_FORM), headers={"Cache-Control": "no-store"})


@core.post("/passwort-vergessen")
async def forgot_post(request: Request):
    form = await request.form()
    ip, rig = _client_ip(request), is_rig(request)
    if reset.ip_throttled(ip):
        return HTMLResponse(_card_page(rig, "Passwort vergessen",
                                       '<p class="lg-err">Zu viele Anfragen – bitte in 15 Minuten nochmal.</p>'), status_code=429)
    # Link IMMER mit fester Adresse – nie aus dem Host-Header (sonst liesse sich der Link umbiegen).
    # Im Hintergrund: die Antwort darf nicht verraten, ob eine Mail verschickt wird (Laufzeit).
    threading.Thread(target=reset.request, args=(str(form.get("who", "")), PUBLIC_URL[rig], "rig" if rig else "ride"),
                     daemon=True).start()
    access.info("-@%s Passwort vergessen angefragt", ip)
    return HTMLResponse(_card_page(rig, "Passwort vergessen", _FORGOT_SENT), headers={"Cache-Control": "no-store"})


def _reset_form(tok, err=""):
    from html import escape as e
    return ((f'<p class="lg-err">{e(err)}</p>' if err else "") +
            '<form method="post" action="/passwort-neu" class="lg-form">'
            f'<input type="hidden" name="t" value="{e(tok)}">'
            '<label>Neues Passwort (mind. 10 Zeichen)<input name="pw" type="password" autocomplete="new-password" required autofocus></label>'
            '<label>Nochmal<input name="pw2" type="password" autocomplete="new-password" required></label>'
            '<button type="submit" class="primary">Passwort speichern</button></form>')


@core.get("/passwort-neu")
def reset_page(request: Request, t: str = ""):
    return HTMLResponse(_card_page(is_rig(request), "Neues Passwort", _reset_form(t)), headers={"Cache-Control": "no-store"})


@core.post("/passwort-neu")
async def reset_post(request: Request):
    form = await request.form()
    tok, pw, pw2 = str(form.get("t", "")), str(form.get("pw", "")), str(form.get("pw2", ""))
    rig = is_rig(request)
    if pw != pw2:
        return HTMLResponse(_card_page(rig, "Neues Passwort", _reset_form(tok, "Die beiden Passwörter sind verschieden.")))
    try:
        name, was_initial = await asyncio.to_thread(reset.consume, tok, pw)
    except admin.AdminError as e:
        return HTMLResponse(_card_page(rig, "Neues Passwort", _reset_form(tok, str(e))))
    except reset.Invalid:
        return HTMLResponse(_card_page(rig, "Neues Passwort", '<p class="lg-err">Der Link ist ungültig oder abgelaufen – '
                                       'bitte <a href="/passwort-vergessen">neu anfordern</a>.</p>'))
    access.info("%s setzt Passwort per Link neu", name)
    if was_initial:
        _tell_admins_pw(name, rig)
    return HTMLResponse(_card_page(rig, "Neues Passwort",
                                   '<p class="lg-ok">Neues Passwort gespeichert. <a href="/login">Jetzt anmelden</a>.</p>'))


def _tell_admins_pw(name, rig):
    """Admins per Mail Bescheid geben: der Nutzer hat sein Startpasswort durch ein eigenes ersetzt."""
    for to in admin.admin_mails():
        _in_background(mailer.send, to, f"{name} hat sein Startpasswort geändert",
                       f"Der Login „{name}“ hat das vom Admin vergebene Startpasswort durch ein eigenes ersetzt "
                       f"({time.strftime('%d.%m.%Y %H:%M')}).\n\nÜbersicht: {PUBLIC_URL[rig]}/admin\n", "rig" if rig else "ride")


def _in_background(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


# ---------------------------------------------------------------- Registrieren mit Freischaltung
def _register_form(rig, err="", v=None):
    from html import escape as e
    v = v or {}
    name = "RigAnalyzer" if rig else "RideAnalyzer"
    return ((f'<p class="lg-err">{e(err)}</p>' if err else
             f'<p class="lg-info-lead">Zugang zum {name} beantragen. Der Login ist erst nach der Freischaltung '
             'durch den Admin nutzbar – du bekommst dann eine Mail.</p>') +
            '<form method="post" action="/registrieren" class="lg-form">'
            f'<label>Benutzername (Kleinbuchstaben/Ziffern)<input name="user" autocomplete="username" autocapitalize="none" '
            'autocorrect="off" spellcheck="false" required minlength="2" maxlength="32" pattern="[A-Za-z0-9]{2,32}" '
            f'value="{e(v.get("user", ""))}" autofocus></label>'
            f'<label>Mailadresse<input name="mail" type="email" autocomplete="email" required value="{e(v.get("mail", ""))}"></label>'
            f'<label>Passwort (mind. {admin.MIN_PW} Zeichen)<input name="pw" type="password" autocomplete="new-password" '
            f'required minlength="{admin.MIN_PW}"></label>'
            '<label>Passwort nochmal<input name="pw2" type="password" autocomplete="new-password" required></label>'
            f'<label>Wer bist du? (optional, z. B. woher du Richy kennst)<input name="note" maxlength="{register.NOTE_MAX}" '
            f'value="{e(v.get("note", ""))}"></label>'
            '<label style="position:absolute;left:-9999px" aria-hidden="true">Bitte leer lassen<input name="website" tabindex="-1" autocomplete="off"></label>'
            '<p class="lg-hint">Mit dem Antrag speichern wir Benutzername, Mailadresse, Notiz und eure IP-Adresse, '
            'um den Zugang einzurichten. Mehr dazu: <a href="/datenschutz" target="_blank" rel="noopener">Datenschutz</a>.</p>'
            '<button type="submit" class="primary">Zugang beantragen</button></form>'
            '<p class="lg-foot"><a href="/login">Zurück zur Anmeldung</a></p>')


@core.get("/registrieren")
def register_page(request: Request):
    if request.state.user:
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_card_page(is_rig(request), "Registrieren", _register_form(is_rig(request))),
                        headers={"Cache-Control": "no-store"})


@core.post("/registrieren")
async def register_post(request: Request):
    form = await request.form()
    ip, rig = _client_ip(request), is_rig(request)
    app_name = "rig" if rig else "ride"
    v = {k: str(form.get(k, "")) for k in ("user", "mail", "note")}
    done = _card_page(rig, "Registrieren", '<p class="lg-ok">Danke! Dein Zugang ist beantragt. Sobald er freigeschaltet ist, '
                      'bekommst du eine Mail und kannst dich mit deinem Passwort anmelden.</p>'
                      '<p class="lg-foot"><a href="/login">Zur Anmeldung</a></p>')
    if form.get("website"):                     # Honeypot: Bots bekommen dieselbe Antwort, aber nichts passiert
        access.info("-@%s Registrierung verworfen (Honeypot)", ip)
        return HTMLResponse(done, headers={"Cache-Control": "no-store"})
    if register.ip_throttled(ip) or register.attempt_throttled(ip):
        return HTMLResponse(_card_page(rig, "Registrieren", '<p class="lg-err">Zu viele Anfragen – bitte später nochmal.</p>'),
                            status_code=429)
    try:
        name = await asyncio.to_thread(register.create, v["user"], v["mail"], str(form.get("pw", "")),
                                       str(form.get("pw2", "")), v["note"], app_name)
    except register.MailTaken as e:
        # gleiche Antwort wie bei Erfolg (keine Auskunft, wer hier Kunde ist); die Adresse erfaehrt es per Mail
        register.note_ip(ip)
        access.info("-@%s Registrierung mit vergebener Mailadresse (Login %s) – Hinweis an die Adresse", ip, e.owner)
        _in_background(mailer.send, v["mail"].strip(), f"Registrierung beim {mailer.APP_NAME[app_name]}",
                       f"Hallo,\n\njemand wollte sich gerade mit dieser Mailadresse beim {mailer.APP_NAME[app_name]} "
                       f"registrieren. Zu der Adresse gibt es schon den Login „{e.owner}“.\n\nPasswort vergessen? "
                       f"{PUBLIC_URL[rig]}/passwort-vergessen\n\nWarst du das nicht, kannst du diese Mail ignorieren.\n",
                       app_name)
        return HTMLResponse(done, headers={"Cache-Control": "no-store"})
    except register.RegisterError as e:
        return HTMLResponse(_card_page(rig, "Registrieren", _register_form(rig, str(e), v)), status_code=422,
                            headers={"Cache-Control": "no-store"})
    register.note_ip(ip)
    access.info("-@%s registriert %s (wartet auf Freischaltung)", ip, name)
    admin.record(name, name, "selbst registriert, wartet auf Freischaltung")
    u = auth.users().get(name, {})
    for to in admin.admin_mails():
        _in_background(mailer.send, to, f"Neue Registrierung: {name} ({mailer.APP_NAME[app_name]})",
                       f"Neue Registrierung im {mailer.APP_NAME[app_name]}:\n\n  Benutzer: {name}\n  Mail:     {u.get('mail', '')}\n"
                       f"  Notiz:    {u.get('note') or '–'}\n  IP:       {ip}\n  Zeit:     {time.strftime('%d.%m.%Y %H:%M')}\n\n"
                       f"Freischalten oder löschen: {PUBLIC_URL[rig]}/admin\n", app_name)
    return HTMLResponse(done, headers={"Cache-Control": "no-store"})


def _tell_approved(name, rig):
    """Nutzer per Mail Bescheid geben: Login freigeschaltet."""
    u = auth.users().get(name, {})
    if not u.get("mail"):
        return
    apps = u.get("apps") or []
    a = apps[0] if len(apps) == 1 else ("rig" if rig else "ride")
    _in_background(mailer.send, u["mail"], f"Dein Zugang zum {mailer.APP_NAME[a]} ist freigeschaltet",
                   f"Hallo {name},\n\ndein Zugang ist freigeschaltet. Du kannst dich jetzt mit deinem Benutzernamen und "
                   f"deinem Passwort anmelden:\n\n  {PUBLIC_URL[a == 'rig']}/login\n\nViel Spaß!\n", a)


def _me_change(request: Request):
    if not request.state.user or request.state.via != "cookie" or not auth.csrf_ok(request):
        raise HTTPException(403, "Bitte die Seite neu laden und erneut versuchen.")
    return request.state.user


@core.post("/api/me/password")
async def api_me_password(request: Request):
    me = _me_change(request)
    b = await request.json()
    ip = _client_ip(request)
    if auth.throttled(ip):   # Durchprobieren mit gestohlenem Cookie bremsen wie beim Login
        raise HTTPException(429, "Zu viele Fehlversuche – bitte in 15 Minuten nochmal.")
    if not await asyncio.to_thread(auth.check, me, str(b.get("old", "")), auth_app(request)):
        auth.failed(ip)
        access.info("%s@%s Passwort ändern: bisheriges Passwort falsch", me, ip)
        raise HTTPException(403, "Das bisherige Passwort stimmt nicht.")
    auth.release(ip)
    was_initial = _admin_call(admin.set_own_password, me, str(b.get("new", "")) or "x")
    access.info("%s aendert sein Passwort", me)
    if was_initial:
        _tell_admins_pw(me, is_rig(request))
    # neues Cookie (das alte ist an den alten Passwort-Hash gebunden)
    value, _exp = auth.make_cookie(me, auth_app(request), False)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, value, httponly=True, secure=True, samesite="lax", path="/")
    return resp


async def _check_own_pw(request: Request, me, pw):
    """Aktuelles Passwort fuer Aenderungen am eigenen Konto (Sperre wie beim Login).
    Hier haengt Teilprojekt E (2FA fuer alle) spaeter den Code an."""
    ip = _client_ip(request)
    if auth.throttled(ip):
        raise HTTPException(429, "Zu viele Fehlversuche – bitte in 15 Minuten nochmal.")
    if not await asyncio.to_thread(auth.check, me, pw, auth_app(request)):
        auth.failed(ip)
        access.info("%s@%s Mailwechsel: Passwort falsch", me, ip)
        raise HTTPException(403, "Das Passwort stimmt nicht.")
    auth.release(ip)


@core.post("/api/me/mail")
async def api_me_mail(request: Request):
    """Nur mit Passwort (seit 0.38.0); seit 0.38.2 gilt eine neue Adresse erst nach Klick auf den Link (mailchange),
    leeren geht sofort."""
    me = _me_change(request)
    b = await request.json()
    await _check_own_pw(request, me, str(b.get("password", "")))
    mail, rig = str(b.get("mail", "")).strip(), is_rig(request)
    app_name = "rig" if rig else "ride"
    if not mail:
        await asyncio.to_thread(mailchange.remove, me, app_name)
        access.info("%s entfernt seine Mailadresse", me)
        return {"status": "removed"}
    try:
        res = await asyncio.to_thread(_admin_call, mailchange.request, me, mail, PUBLIC_URL[rig], app_name)
    except mailchange.Throttled:
        raise HTTPException(429, "Schon drei Links in der letzten Stunde – bitte später nochmal.") from None
    except mailchange.SendFailed:
        raise HTTPException(502, "Die Mail an die neue Adresse ging nicht raus – bitte später nochmal.") from None
    if res["status"] == "sent":
        access.info("%s fordert Mailwechsel an", me)
    return res


@core.delete("/api/me/mail")
def api_me_mail_cancel(request: Request):
    me = _me_change(request)
    if mailchange.cancel(me):
        access.info("%s bricht Mailwechsel ab", me)
    return {"ok": True}


def _mailconfirm_form(tok):
    from html import escape as e
    return ('<p>Mit dem Knopf gilt die neue Mailadresse für deinen Login.</p>'
            '<form method="post" action="/mail-bestaetigen" class="lg-form">'
            f'<input type="hidden" name="t" value="{e(tok)}">'
            '<button type="submit" class="primary">Adresse bestätigen</button></form>')


@core.get("/mail-bestaetigen")
def mailconfirm_page(request: Request, t: str = ""):
    # Nur anzeigen: Mail-Scanner rufen Links vorab auf – verbraucht wird erst per Knopf (POST)
    return HTMLResponse(_card_page(is_rig(request), "Mailadresse bestätigen", _mailconfirm_form(t)),
                        headers={"Cache-Control": "no-store"})


def _unsub_form(tok):
    from html import escape as e
    return ('<p>Mit dem Knopf bekommst du diese Mails nicht mehr. Wieder einschalten geht unter Mein Konto.</p>'
            '<form method="post" action="/abbestellen" class="lg-form">'
            f'<input type="hidden" name="t" value="{e(tok)}">'
            '<button type="submit" class="primary">Abbestellen</button></form>')


@core.get("/abbestellen")
def unsubscribe_page(request: Request, t: str = ""):
    """Abmeldelink aus Sammelmail/Newsletter (0.50.0, Ticket #16) – ohne Login. Nur anzeigen: Mail-Scanner rufen
    Links vorab auf, abbestellt wird erst per Knopf (POST) bzw. per One-Click (List-Unsubscribe-Post)."""
    rig = is_rig(request)
    try:
        _, kind = notify.check(t, "rig" if rig else "ride")
    except notify.Invalid:
        return HTMLResponse(_card_page(rig, "Abbestellen", '<p class="lg-err">Der Link ist ungültig. Abbestellen geht auch unter '
                                       '<a href="/konto">Mein Konto</a>.</p>'), headers={"Cache-Control": "no-store"})
    return HTMLResponse(_card_page(rig, f"„{notify.KINDS[kind]}“ abbestellen", _unsub_form(t)), headers={"Cache-Control": "no-store"})


@core.post("/abbestellen")
async def unsubscribe_post(request: Request):
    """Formular (t im Formular) oder One-Click aus dem Mailprogramm (RFC 8058: t in der Adresse)."""
    form = await request.form()
    rig = is_rig(request)
    tok = str(form.get("t") or request.query_params.get("t") or "")
    try:
        user, kind = notify.check(tok, "rig" if rig else "ride")
    except notify.Invalid:
        return HTMLResponse(_card_page(rig, "Abbestellen", '<p class="lg-err">Der Link ist ungültig.</p>'),
                            status_code=400, headers={"Cache-Control": "no-store"})
    await asyncio.to_thread(notify.set_kind, user, kind, False)
    access.info("%s bestellt %s ab", user, kind)
    return HTMLResponse(_card_page(rig, "Abbestellt", f'<p class="lg-ok">„{notify.KINDS[kind]}“ ist abbestellt.</p>'
                                   '<p><a href="/konto">Zu Mein Konto</a></p>'), headers={"Cache-Control": "no-store"})


@core.get("/qa/postfach")
def qa_postfach(request: Request):
    """QA-Postfach (0.40.1, Ticket #10): Mails, die QA sonst verwerfen wuerde (qapost.py). Nur STAGE=qa (sonst 404,
    auch in Prod), nur Admins per Anmelde-Cookie mit bestandener 2FA – die Mails enthalten gueltige Token-Links."""
    if STAGE != "qa" or not auth.is_admin(request.state.user) or request.state.via != "cookie":
        raise HTTPException(404, "Nicht gefunden.")
    if not twofa.enabled(request.state.user):
        return RedirectResponse("/2fa-einrichten", status_code=303)
    if not twofa.mfa_ok(request, request.state.user):
        return RedirectResponse("/zweiter-faktor?next=/qa/postfach", status_code=303)
    return HTMLResponse(_card_page(is_rig(request), "📬 QA-Postfach", qapost.page_body(qapost.items())),
                        headers={"Cache-Control": "no-store"})


@core.post("/mail-bestaetigen")
async def mailconfirm_post(request: Request):
    from html import escape as e
    form = await request.form()
    rig = is_rig(request)
    try:
        name, mail = await asyncio.to_thread(mailchange.confirm, str(form.get("t", "")))
    except mailchange.Invalid:
        return HTMLResponse(_card_page(rig, "Mailadresse bestätigen",
                                       '<p class="lg-err">Der Link ist ungültig oder abgelaufen – bitte unter '
                                       '<a href="/konto">Mein Konto</a> neu anfordern.</p>'), headers={"Cache-Control": "no-store"})
    access.info("%s bestaetigt Mailwechsel", name)
    return HTMLResponse(_card_page(rig, "Mailadresse bestätigt",
                                   f'<p class="lg-ok">Mailadresse bestätigt: <b>{e(mail)}</b>. '
                                   '„Passwort vergessen“ geht ab jetzt dorthin.</p><p><a href="/konto">Zu Mein Konto</a></p>'),
                        headers={"Cache-Control": "no-store"})


@core.post("/api/me/logout-all")
def api_me_logout_all(request: Request):
    """Ueberall abmelden (0.38.0): alle Sitzungen dieses Logins auf allen Geraeten beenden."""
    me = _me_change(request)
    auth.logout_everywhere(me)
    access.info("%s meldet sich ueberall ab", me)
    events.log("logout_all", me, auth_app(request))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return resp


_ADMIN_BODY = '''
<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css">
<div id="adm-cards" class="kt-cards adm-cards">
<section class="card" data-acard="logins" data-title="Logins" data-wide="1">
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
    <h3 style="margin:0;flex:1">Logins</h3>
    <button type="button" class="primary" id="adm-new">＋ Neuer Login</button>
  </div>
  <p id="adm-msg" hidden></p>
  <input type="search" id="adm-find" class="adm-find" placeholder="Login, Mail oder Gruppe suchen …" aria-label="Logins durchsuchen">
  <div id="adm-rows" class="al-list"></div>
  <p class="hint" id="adm-none" hidden>Kein Login passt zur Suche.</p>
</section>
<section class="card" data-acard="aktiv" data-title="Jetzt aktiv" data-col="l">
  <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px">
    <h3 style="margin:0;flex:1">Jetzt aktiv</h3><span class="hint" id="act-upd"></span>
  </div>
  <div class="act-grid">
    <!--rig--><div id="act-map" class="act-map"></div><!--/rig-->
    <div id="act-list" class="act-list"><p class="hint">lädt …</p></div>
  </div>
  <!--rig--><p class="hint full-only">Position nur aus RigNavi mit Zustimmung des Fahrers – während der Fahrt und solange die App offen ist –, auf ~1 km gerundet, nach 24 h gelöscht.</p><!--/rig-->
</section>
<!--ober--><section class="card full-only" id="fb-card" data-acard="rueckmeldungen" data-title="Rückmeldungen" data-col="r" hidden>
  <h3 style="margin-top:0">Rückmeldungen</h3>
  <p class="hint">Was angemeldete Nutzer über „💬 Rückmeldung“ schicken – nur diese App. Neue kommen auch in den Alarm-Kanal.</p>
  <div id="fb-list"><p class="hint">lädt …</p></div>
</section><!--/ober-->
<!--ober--><section class="card full-only" data-acard="gruppen" data-title="Gruppen" data-col="l">
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
    <h3 style="margin:0;flex:1">Gruppen</h3>
    <button type="button" class="ghost" id="grp-new">＋ Neue Gruppe</button>
  </div>
  <p class="hint">Gruppen-Admins verwalten nur die Logins ihrer Gruppen – und nur in den Apps, die sie verwalten dürfen.</p>
  <div id="grp-list"></div>
</section><!--/ober-->
<!--ober--><!--rig--><section class="card full-only" id="cost-card" data-acard="kosten" data-title="Kosten" data-col="r" hidden>
  <h3 style="margin-top:0">Kosten</h3>
  <div id="cost-head" class="cost-head"></div>
  <p class="hint" id="cost-since"></p>
  <div id="cost-bars" class="cost-bars"></div>
  <div id="cost-legend" class="cost-legend"></div>
  <div class="tbl-scroll"><table class="tbl"><thead><tr><th>Account</th><th>DL GB</th><th>Ø Speicher GB</th><th>Analysen</th><th>Planungen</th><th>Routen</th><th>Anteil</th><th>Verbrauch</th><th>Kosten</th><th>Gewinn m. Abo</th><th>Preis o. Abo</th><th>Gewinn o. Abo</th></tr></thead>
    <tbody id="cost-rows"></tbody><tfoot id="cost-foot"></tfoot></table></div>
  <p class="hint" id="cost-sum"></p>
  <div id="cost-dev" class="cost-dev"></div>
  <p class="hint">Summen gelten nur für die aufgelisteten Rig-Accounts; die Fixkosten werden auf alle aktiven Accounts (Ride + Rig) verteilt.</p>
  <details class="cost-prices-box"><summary>Preise</summary>
    <div id="cost-prices" class="cost-prices"></div>
    <div style="display:flex;gap:10px;align-items:center;margin-top:10px"><button type="button" class="primary" id="cost-save">Speichern</button>
      <span id="cost-msg" hidden></span></div>
  </details>
</section><!--/rig--><!--/ober-->
<!--ober--><section class="card full-only" id="nl-card" data-acard="newsletter" data-title="Newsletter" data-wide="1" data-fold="1">
  <div class="kt-top"><h3 style="margin:0">✉️ Newsletter</h3><a class="primary small" href="/admin/newsletter">✏️ Newsletter schreiben</a></div>
  <p class="hint" id="nl-count">lädt …</p>
  <details class="kt-fold"><summary>Bisher verschickt</summary><ul class="kt-liste" id="nl-log"></ul></details>
</section><!--/ober-->
<section class="card" data-acard="aenderungen" data-title="Letzte Änderungen" data-wide="1">
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <h3 style="margin:0;flex:1">Letzte Änderungen</h3>
    <a class="ghost small full-only" href="/api/admin/report" target="_blank" rel="noopener">📄 Tagesbericht von gestern</a></div>
  <div class="tbl-scroll"><table class="tbl"><thead><tr><th>Wann</th><th>Wer</th><th>Login / Gruppe</th><th>Was</th></tr></thead>
    <tbody id="log-rows"></tbody></table></div>
</section>
<section class="card" data-acard="versionen" data-title="Versionen" data-wide="1" data-fold="1">
  <h3 style="margin-top:0">Versionen</h3>
  <div id="ver-run" class="ver-run"><p class="hint">lädt …</p></div>
  <div id="ver-list"></div>
</section>
</div>
<p class="hint kt-reset">Kacheln lassen sich mit ⠿ ziehen, mit ↑/↓ verschieben, mit ⇄ in die andere Spalte setzen, mit ⇔ halb oder ganz breit stellen und mit ▾ einklappen – gemerkt an deinem Login.
<button type="button" class="linkish" id="adm-reset">Anordnung zurücksetzen</button></p>
<dialog class="dlg" id="adm-dlg">
  <form id="adm-form" class="lg-form">
    <h3 style="margin-top:0">Neuer Login</h3>
    <label>Name (Kleinbuchstaben/Ziffern)<input name="uname" required pattern="[a-z0-9]{2,32}" autocapitalize="none"></label>
    <label>Mail (optional)<input name="mail" type="email"></label>
    <div hidden><!--ride--><input type="checkbox" name="ride" checked><!--/ride--><!--rig--><input type="checkbox" name="rig" checked><!--/rig--></div>
    <label>Gruppe<select name="group" id="adm-new-group"></select></label>
    <label class="lg-chk"><input type="checkbox" name="send"> Zugangsdaten per Mail schicken</label>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button type="button" class="ghost" id="adm-cancel">Abbrechen</button><button type="submit" class="primary">Anlegen</button></div>
  </form>
</dialog>
<dialog class="dlg" id="adm-rights">
  <form id="adm-rights-form" class="lg-form">
    <h3 style="margin-top:0" id="adm-rights-title"></h3>
    <label class="lg-chk"><input type="checkbox" name="ober"> Oberadmin (darf alles, auch Gruppen und Admin-Rechte)</label>
    <div id="adm-rights-sub">
      <p class="hint">Gruppen-Admin für diese Apps …</p>
      <div><!--ride--><label class="pill"><input type="checkbox" name="ride"> Ride</label><!--/ride--><!--rig--><label class="pill"><input type="checkbox" name="rig"> Rig</label><!--/rig--></div>
      <p class="hint">… in diesen Gruppen:</p>
      <div id="adm-rights-groups"></div>
      <p class="lg-err" id="adm-rights-warn" hidden>Mindestens eine Gruppe anhaken – ohne Gruppe sieht ein Gruppen-Admin niemanden.</p>
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button type="button" class="ghost" id="adm-rights-cancel">Abbrechen</button><button type="submit" class="primary">Speichern</button></div>
  </form>
</dialog>
<dialog class="dlg" id="adm-hist">
  <h3 id="adm-hist-title" style="margin-top:0"></h3>
  <table class="tbl"><thead><tr><th>Wann</th><th>IP</th><th>Gerät</th><th></th></tr></thead><tbody id="adm-hist-rows"></tbody></table>
  <div style="text-align:right;margin-top:10px"><button type="button" class="primary" id="adm-hist-close">Schließen</button></div>
</dialog>
<dialog class="dlg" id="adm-pw">
  <h3 id="adm-pw-title" style="margin-top:0"></h3>
  <p>Login <b id="adm-pw-name"></b>, Passwort:</p>
  <p class="pwshow" id="adm-pw-value"></p>
  <p class="hint" id="adm-pw-extra"></p>
  <div style="text-align:right"><button type="button" class="primary" id="adm-pw-close">Fertig</button></div>
</dialog>'''  # noqa: E501 – HTML-Vorlage, ein Umbruch wuerde die Ausgabe aendern

_KONTO_BODY = '''
<section class="card kt-account" data-kcard="zugang" data-title="🔑 Zugang">
  <p class="kt-who">Angemeldet als <b id="kt-user"></b></p>
  <details class="kt-fold" id="kt-pw"><summary>🔑 Passwort ändern</summary>
  <form id="kt-pw-form" class="lg-form" style="max-width:420px">
    <label>Bisheriges Passwort<input name="old" type="password" autocomplete="current-password" required></label>
    <label>Neues Passwort (mind. 10 Zeichen)<input name="new1" type="password" autocomplete="new-password" minlength="10" required></label>
    <label>Nochmal<input name="new2" type="password" autocomplete="new-password" minlength="10" required></label>
    <button type="submit" class="primary">Passwort speichern</button>
    <p id="kt-pw-msg" hidden></p>
  </form></details>
  <details class="kt-fold" id="kt-mailbox"><summary>✉️ Mailadresse <span class="hint" id="kt-mail-sum"></span></summary>
  <form id="kt-mail-form" class="lg-form" style="max-width:420px">
    <p class="hint">Für „Passwort vergessen“: Dorthin kommt der Link für ein neues Passwort.
    Eine neue Adresse gilt erst, wenn du den Link in der Mail an sie bestätigst.</p>
    <label>Mail<input id="kt-mail" name="mail" type="email" autocomplete="email"></label>
    <label>Aktuelles Passwort<input id="kt-mail-pw" name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit" class="primary">Bestätigungslink schicken</button>
    <p id="kt-mail-msg" hidden></p>
    <div id="kt-mail-pending" class="hint" hidden><span id="kt-mail-pending-text"></span>
      <button type="button" class="linkish" id="kt-mail-again">Nochmal schicken</button>
      <button type="button" class="linkish" id="kt-mail-cancel">Abbrechen</button></div>
  </form></details>
  <details class="kt-fold" id="kt-sessions"><summary>🚪 Überall abmelden</summary>
    <p class="hint">Beendet deine Anmeldung auf allen Geräten und Browsern – auch hier. Sinnvoll, wenn ein Gerät weg ist
    oder du dich an einem fremden Rechner nicht abgemeldet hast. Apps mit gespeichertem Passwort bleiben angemeldet (dafür: Passwort ändern).</p>
    <button type="button" class="ghost" id="kt-logout-all">Überall abmelden</button>
    <p id="kt-sess-msg" hidden></p>
  </details>
</section>'''

_KONTO_MAIL = '''
<section class="card" id="kt-notify" data-kcard="mail" data-title="✉️ Benachrichtigungen">
  <h4 style="margin-top:0">✉️ Benachrichtigungen</h4>
  <div id="kt-notify-ask" class="kt-ask" hidden>
    <p><b>Wollt ihr Mails zu Neuerungen?</b> Höchstens eine am Abend, wenn es etwas Neues gibt – dazu ab und zu ein Newsletter.
    Abbestellen geht jederzeit mit einem Klick.</p>
    <button type="button" class="primary" id="kt-notify-yes">Ja, gern</button>
    <button type="button" class="ghost" id="kt-notify-no">Nein, danke</button>
  </div>
  <label class="kt-switch"><input type="checkbox" id="kt-notify-releases"> <span><b>Neue Versionen</b> – abends eine Sammelmail,
    wenn es an dem Tag Neuerungen gab</span></label>
  <label class="kt-switch"><input type="checkbox" id="kt-notify-newsletter"> <span><b>Newsletter</b> – ab und zu Neuigkeiten und Tipps</span></label>
  <p class="hint" id="kt-notify-nomail" hidden>Dafür braucht es eine Mailadresse – oben unter „🔑 Zugang“ eintragen.</p>
  <p class="hint">Mails zu Passwort und Zugang kommen immer. Jede andere Mail hat unten einen Abmeldelink.</p>
  <p id="kt-notify-msg" hidden></p>
</section>'''

_KONTO_DATEN = '''
<section class="card" id="kt-daten" data-kcard="daten" data-title="🗂 Meine Daten">
  <div class="kt-top"><h4 style="margin:0">🗂 Meine Daten</h4>
    <a class="ghost small" href="/api/me/data" download>⬇ Alles herunterladen (JSON)</a></div>
  <p class="hint">Was zu deinem Login gespeichert ist und was wir zählen – nur Zahlen, ohne Ziele oder Strecken.
  Der Download enthält alles vollständig, nur Passwort und 2FA-Schlüssel nicht.</p>
  <div class="kt-daten-grid">
    <div><h5>Gespeichert</h5><dl id="kt-daten-stored"><dt>lädt …</dt></dl></div>
    <div><h5>Gezählt diesen Monat</h5><dl id="kt-daten-month"></dl></div>
  </div>
  <details class="kt-fold" id="kt-logins"><summary>Anmeldungen der letzten 30 Tage <span class="hint" id="kt-logins-sum"></span></summary>
    <table class="kt-logins"><thead><tr><th>Zeit</th><th>Gerät</th><th>IP</th><th></th></tr></thead><tbody id="kt-logins-body"></tbody></table>
  </details>
</section>'''

_KONTO_TOUREN = '''
<section class="card" id="kt-touren" data-kcard="touren" data-title="📂 Meine Touren">
  <div class="kt-top"><h4 style="margin:0">📂 Meine Touren</h4><span class="hint" id="kt-touren-sum">lädt …</span></div>
  <ul class="kt-liste" id="kt-touren-liste"></ul>
  <p id="kt-touren-msg" hidden></p>
</section>'''

_KONTO_RESET = '''
<p class="hint kt-reset">Kacheln lassen sich mit ⠿ ziehen, mit ↑/↓ verschieben und mit ▾ einklappen – gemerkt an deinem Login.
<button type="button" class="linkish" id="kt-reset">Anordnung zurücksetzen</button></p>'''

_KONTO_RIG = '''
<section class="card" id="kt-rignavi" data-kcard="rignavi" data-title="📱 RigNavi">
  <h4 style="margin-top:0">📱 RigNavi</h4>
  <p class="hint">Die Navi-Einstellungen – Tempo-Warnung fürs Gespann, Positionsfreigabe, Nachtmodus – findest du in der App unter ⚙.
  Sie gelten für deinen Login auf jedem Handy. <a href="/app.html">App herunterladen und Anleitung</a></p>
</section>'''


@core.get("/admin")
def admin_page(request: Request):
    if not auth.is_admin(request.state.user) or request.state.via != "cookie":
        raise HTTPException(404, "Nicht gefunden.")
    if not twofa.enabled(request.state.user):
        return RedirectResponse("/2fa-einrichten", status_code=303)
    if not twofa.mfa_ok(request, request.state.user):
        return RedirectResponse("/zweiter-faktor?next=/admin", status_code=303)
    rig = is_rig(request)
    ober = _scope(request.state.user) == "all"
    desc = (f"Logins für den {'RigAnalyzer' if rig else 'RideAnalyzer'}: anlegen, Passwort neu setzen, Mail, Zugang, Admin-Recht, sperren."
            if ober else
            "Gruppen-Admin: Logins deiner Gruppen anlegen, Passwort neu setzen, Mail, Apps, sperren.")
    body = _only_app(_ADMIN_BODY, rig)
    if not ober:   # Gruppen, Kosten, Rückmeldungen gar nicht erst ausliefern (Dashboard-Kacheln je Rolle)
        body = re.sub(r"<!--ober-->.*?<!--/ober-->", "", body, flags=re.S)
        # ohne Rückmeldungen daneben stünde „Jetzt aktiv“ halb breit neben einer leeren Spalte → Standard breit
        body = body.replace('data-acard="aktiv" data-title="Jetzt aktiv" data-col="l"',
                            'data-acard="aktiv" data-title="Jetzt aktiv" data-col="l" data-wide="1"')
    body = body.replace("<!--ober-->", "").replace("<!--/ober-->", "")
    return HTMLResponse(_page(is_rig(request), "Admin", desc + " Jede Änderung steht im Zugriffsprotokoll.", body,
                              scripts=("vendor/leaflet/leaflet.js", "admin.js", "admin-feedback.js")
                              + (("admin-costs.js",) if rig else ()) + (("admin-newsletter.js",) if ober else ()) + ("cards.js", "admin-cards.js")),
                        headers={"Cache-Control": "no-store"})


@core.get("/konto")
def konto_page(request: Request):
    rig = is_rig(request)
    return HTMLResponse(_page(rig, "Mein Konto", "Deine Touren, dein Zugang und deine Daten." if rig
                              else "Dein Zugang und deine Daten.",
                              # „Meine Touren“ und RigNavi nur beim RigAnalyzer; „Meine Daten“ in beiden
                              # Kacheln verschiebbar (cards.js + konto-cards.js, Anordnung am Login unter prefs „konto“)
                              '<div id="kt-cards" class="kt-cards">' + (_KONTO_TOUREN if rig else "") + _KONTO_BODY
                              + _KONTO_MAIL + _KONTO_DATEN + (_KONTO_RIG if rig else "") + '</div>' + _KONTO_RESET,
                              scripts=("konto.js", "konto-notify.js", "pwtoggle.js", "konto-daten.js", "cards.js", "konto-cards.js")
                              + (("static-rig/teilen.js", "static-rig/navistatus.js", "static-rig/konto-touren.js") if rig else ())),
                        headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- Zweiter Faktor fuer Admins
def _set_session(resp, user, app, remember):
    """Anmelde-Cookies setzen (Sitzung, CSRF und – fuer Admins mit 2FA – die Admin-Sitzung)."""
    value, exp = auth.make_cookie(user, app, remember)
    age = (exp - int(time.time())) if remember else None
    resp.set_cookie(auth.COOKIE, value, max_age=age, httponly=True, secure=True, samesite="lax", path="/")
    resp.set_cookie(auth.CSRF_COOKIE, auth.new_csrf(), max_age=age, httponly=False, secure=True, samesite="strict", path="/")
    return exp, age


def _set_mfa(resp, user, exp, age):
    resp.set_cookie(twofa.MFA_COOKIE, twofa.make_mfa(user, exp), max_age=age, httponly=True, secure=True,
                    samesite="strict", path="/")


_CODE_FORM = ('<form method="post" action="/zweiter-faktor" class="lg-form">'
              '<input type="hidden" name="next" value="{nxt}">'
              '<label>6-stelliger Code aus der App – oder ein Notfall-Code'
              '<input name="code" inputmode="numeric" autocomplete="one-time-code" autocapitalize="none" required autofocus></label>'
              '<button type="submit" class="primary">Bestätigen</button></form>'
              '<p class="lg-foot"><a href="/login">Abbrechen</a></p>')


def _pending_user(request: Request):
    """Wer gerade den Code eingeben darf: Zwischen-Cookie nach dem Passwort, oder eine bestehende Sitzung."""
    p = twofa.read_pending(request.cookies.get(twofa.PEND_COOKIE))
    if p:
        return p
    if request.state.user and request.state.via == "cookie":
        return request.state.user, auth_app(request), False
    return None


@core.get("/zweiter-faktor")
def twofa_page(request: Request, next: str = ""):
    from html import escape as e
    if not _pending_user(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(_card_page(is_rig(request), "Zweiter Faktor",
                                   '<p class="lg-ok">Admin-Login: bitte den Code aus deiner Authenticator-App eingeben.</p>'
                                   + _CODE_FORM.format(nxt=e(_safe_next(next)))), headers={"Cache-Control": "no-store"})


@core.post("/zweiter-faktor")
async def twofa_post(request: Request):
    from html import escape as e
    form = await request.form()
    nxt, ip, rig = _safe_next(str(form.get("next", ""))), _client_ip(request), is_rig(request)
    p = _pending_user(request)
    if not p:
        return RedirectResponse("/login", status_code=303)
    user, app_, remember = p
    if auth.throttled(ip):
        return HTMLResponse(_card_page(rig, "Zweiter Faktor", '<p class="lg-err">Zu viele Fehlversuche – bitte in 15 Minuten nochmal.</p>'),
                            status_code=429)
    # zusaetzlich je Login (5 in 15 Min.) – sonst probiert ein Angreifer mit Passwort und vielen IPs die Codes durch.
    # Gesperrt: Zwischen-Cookie weg, es geht erst nach erneuter Passwort-Eingabe weiter.
    if auth.user_throttled(user):
        auth.release(ip)
        access.info("%s@%s 2FA gesperrt (zu viele falsche Codes fuer diesen Login)", user, ip)
        loginwatch.login(user, app_, ip, ok=False, detail="2FA gesperrt")
        resp = HTMLResponse(_card_page(rig, "Zweiter Faktor", '<p class="lg-err">Zu viele falsche Codes – bitte in 15 Minuten neu '
                                       'anmelden.</p>'), status_code=429)
        resp.delete_cookie(twofa.PEND_COOKIE, path="/")
        return resp
    if not await asyncio.to_thread(twofa.verify, user, str(form.get("code", ""))):
        auth.failed(ip)
        loginwatch.login(user, app_, ip, ok=False, detail="2FA-Code falsch")
        access.info("%s@%s 2FA-Code falsch", user, ip)
        return HTMLResponse(_card_page(rig, "Zweiter Faktor", '<p class="lg-err">Code stimmt nicht (oder wurde schon benutzt).</p>'
                                       + _CODE_FORM.format(nxt=e(nxt))), status_code=401)
    auth.release(ip)
    auth.user_release(user)
    access.info("%s@%s 2FA bestanden", user, ip)
    resp = RedirectResponse(nxt, status_code=303)
    exp, age = _set_session(resp, user, app_, remember)
    _set_mfa(resp, user, exp, age)
    resp.delete_cookie(twofa.PEND_COOKIE, path="/")
    return resp


@core.get("/2fa-einrichten")
def twofa_setup_page(request: Request):
    if not auth.is_admin(request.state.user) or request.state.via != "cookie":
        raise HTTPException(404, "Nicht gefunden.")
    user = request.state.user
    if twofa.enabled(user):
        return RedirectResponse("/admin", status_code=303)
    secret = twofa.begin_setup(user)
    body = f'''
<section class="card">
  <h3 style="margin-top:0">Zweiten Faktor einrichten</h3>
  <p>Admins melden sich mit Passwort <b>und</b> einem Code aus einer Authenticator-App an (z. B. Aegis, 2FAS, Google Authenticator).
     Den QR-Code mit der App scannen – oder den Schlüssel abtippen –, dann den angezeigten 6-stelligen Code eingeben.</p>
  <div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">
    <div style="background:#fff;padding:10px;border-radius:16px">{twofa.qr_svg(user, secret)}</div>
    <div><p class="hint">Schlüssel zum Abtippen:</p><p class="pwshow" data-secret="{secret}">{" ".join(secret[i:i + 4] for i in range(0, len(secret), 4))}</p></div>
  </div>
  <form method="post" action="/2fa-einrichten" class="lg-form" style="max-width:360px">
    <label>Code aus der App<input name="code" inputmode="numeric" autocomplete="one-time-code" required autofocus></label>
    <button type="submit" class="primary">Bestätigen</button>
  </form>
</section>'''  # noqa: E501 – HTML-Vorlage, ein Umbruch wuerde die Ausgabe aendern
    return HTMLResponse(_page(is_rig(request), "2FA", "Ab jetzt schützt ein zweiter Faktor den Admin-Bereich.", body),
                        headers={"Cache-Control": "no-store"})


@core.post("/2fa-einrichten")
async def twofa_setup_post(request: Request):
    if not auth.is_admin(request.state.user) or request.state.via != "cookie":
        raise HTTPException(404, "Nicht gefunden.")
    user, ip = request.state.user, _client_ip(request)
    form = await request.form()
    if auth.throttled(ip):
        raise HTTPException(429, "Zu viele Fehlversuche – bitte in 15 Minuten nochmal.")
    try:
        codes = await asyncio.to_thread(twofa.confirm_setup, user, str(form.get("code", "")))
    except twofa.Invalid:
        auth.failed(ip)
        return RedirectResponse("/2fa-einrichten", status_code=303)
    auth.release(ip)
    access.info("%s richtet 2FA ein", user)
    body = ('<section class="card"><h3 style="margin-top:0">✓ Zweiter Faktor aktiv</h3>'
            '<p>Das sind deine <b>Notfall-Codes</b> – jeder gilt einmal, falls das Handy weg ist. Jetzt ausdrucken oder '
            'im Passwort-Tresor speichern, sie werden nicht nochmal angezeigt.</p><ul class="pwshow" style="columns:2;list-style:none">'
            + "".join(f'<li class="bk">{c}</li>' for c in codes) +
            '</ul><p><a class="primary" href="/admin">Weiter zum Admin-Bereich</a></p></section>')
    resp = HTMLResponse(_page(is_rig(request), "2FA", "Eingerichtet.", body), headers={"Cache-Control": "no-store"})
    _, exp = auth.make_cookie(user, auth_app(request), False)
    _set_mfa(resp, user, exp, None)
    return resp


@core.get("/api/me")
def api_me(request: Request):
    u = auth.users().get(request.state.user or "", {})
    return {"user": request.state.user, "admin": admin._admin_out(u), "mail": u.get("mail", ""),
            "mail_pending": mailchange.pending(request.state.user) if request.state.user else None,
            "settings": auth.settings(request.state.user),
            "notify": notify.settings(request.state.user) if request.state.user else None}


# ---------------------------------------------------------------- Meine Daten (DSGVO-Auskunft)
def _me_read(request: Request):
    if not request.state.user:
        raise HTTPException(401, "Bitte anmelden.")
    return request.state.user


@core.get("/api/me/data")
def api_me_data(request: Request):
    import mydata
    me = _me_read(request)
    access.info("%s laedt seine Daten herunter", me)
    name = re.sub(r"[^a-z0-9._-]", "_", me.lower())
    return JSONResponse(mydata.export(me), headers={
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="meine-daten-{name}-{time.strftime("%Y-%m-%d")}.json"'})


@core.get("/api/me/data/overview")
def api_me_data_overview(request: Request):
    import mydata
    return JSONResponse(mydata.overview(_me_read(request), auth_app(request)), headers={"Cache-Control": "no-store"})


@core.post("/api/me/settings")
async def api_me_settings(request: Request):
    """Einstellungen am Login (seit v0.10). Webseite: Cookie + CSRF; RigNavi: Basic-Auth."""
    me = request.state.user
    if not me:
        raise HTTPException(401, "Bitte anmelden.")
    if request.state.via == "cookie" and not auth.csrf_ok(request):
        raise HTTPException(403, "Bitte die Seite neu laden und erneut versuchen.")
    try:
        b = await request.json()
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    if not auth.settings_valid(b):
        raise HTTPException(422, "Unbekannte Einstellung.")
    res = await asyncio.to_thread(auth.set_settings, me, b)
    access.info("%s aendert Einstellungen %s", me, b)
    return {"settings": res}


# ---------------------------------------------------------------- Vorlieben am Login
@core.get("/api/me/prefs")
def api_me_prefs(request: Request):
    """Fahrzeugprofile, Zoom, Reiter/Ebenen – Webseite und RigNavi teilen sie (prefs.py)."""
    import prefs
    if not request.state.user:
        raise HTTPException(401, "Bitte anmelden.")
    return prefs.get(request.state.user)


@core.put("/api/me/prefs")
async def api_me_prefs_put(request: Request):
    import prefs
    me = request.state.user
    if not me:
        raise HTTPException(401, "Bitte anmelden.")
    if request.state.via == "cookie" and not auth.csrf_ok(request):
        raise HTTPException(403, "Bitte die Seite neu laden und erneut versuchen.")
    try:
        res = await asyncio.to_thread(prefs.put, me, await request.json())
    except prefs.PrefsError as e:
        raise HTTPException(422, str(e)) from None
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    return res


# ---------------------------------------------------------------- Admin
def _admin(request: Request, change=False):
    """Nur Admins per Anmelde-Cookie (sonst 404 – keine Auskunft), nur mit bestandener 2FA (sonst 403),
    aendernde Aufrufe zusaetzlich mit CSRF."""
    if not auth.is_admin(request.state.user) or request.state.via != "cookie":
        raise HTTPException(404, "Nicht gefunden.")
    if not twofa.mfa_ok(request, request.state.user):
        raise HTTPException(403, "Admin-Bereich nur mit zweitem Faktor – bitte /admin neu öffnen.")
    if change and not auth.csrf_ok(request):
        raise HTTPException(403, "Bitte die Seite neu laden und erneut versuchen.")
    return request.state.user


def _admin_call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except admin.AdminError as e:
        raise HTTPException(e.status, str(e)) from None


@core.get("/api/admin/activity")
def api_admin_activity(request: Request):
    scope = _scope(_admin(request))
    names = [n for n, u in sorted(auth.users().items()) if admin.visible(u, scope, auth_app(request))]
    users = activity.snapshot(names)
    if scope != "all" or not is_rig(request):
        users = [{**u, "navi": None} for u in users]       # RigNavi-Positionen nur Oberadmin, nur beim RigAnalyzer
    return {"users": users, "logins": {n: activity.logins(n) for n in names}}


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ---------------------------------------------------------------- Rueckmeldungen
_FEEDBACK_BODY = """<section class="card">
  <p class="hint">Fehler gefunden, eine Idee oder einfach Lob? Schreib mir – die Nachricht landet direkt bei Richy.
  Antworten kommen an die Mailadresse deines Kontos.</p>
  <form id="fb-form" class="lg-form">
    <label>Art
      <select name="kind" required>
        <option value="idee">💡 Idee</option><option value="fehler">🐞 Fehler</option>
        <option value="lob">👍 Lob</option><option value="sonstiges">💬 Sonstiges</option>
      </select></label>
    <label>Deine Nachricht<textarea name="text" rows="7" minlength="3" maxlength="4000" required></textarea></label>
    <label class="lg-chk"><input type="checkbox" name="page" value="1" checked> Seite mitschicken, von der ich komme</label>
    <button type="submit" class="primary">Absenden</button>
  </form>
  <p id="fb-msg" hidden></p>
</section>"""


@core.get("/rueckmeldung")
def feedback_page(request: Request):
    if not request.state.user:
        return RedirectResponse("/login?next=/rueckmeldung", status_code=303)
    return HTMLResponse(_page(is_rig(request), "Rückmeldung", "Fehler, Ideen, Lob – direkt an Richy.",
                              _FEEDBACK_BODY, scripts=("feedback.js",)), headers={"Cache-Control": "no-store"})


@core.post("/api/feedback")
async def api_feedback(request: Request):
    me = request.state.user
    if not me:
        raise HTTPException(401, "Bitte anmelden.")
    if request.state.via == "cookie" and not auth.csrf_ok(request):
        raise HTTPException(403, "Bitte die Seite neu laden und erneut versuchen.")
    try:
        b = await request.json()
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    if not isinstance(b, dict):
        raise HTTPException(400, "Ungültige Anfrage.")
    try:
        e = feedback.add(me, auth_app(request), b.get("kind"), b.get("text"), b.get("page") or "",
                         mail=(auth.users().get(me) or {}).get("mail"))
    except feedback.FeedbackError as err:
        raise HTTPException(422, str(err)) from None
    except feedback.TooMany:
        raise HTTPException(429, f"Heute schon {feedback.PER_DAY} Rückmeldungen – bitte morgen wieder.") from None
    access.info("%s schickt Rueckmeldung (%s)", who(request), e["kind"])
    return {"ok": True, "id": e["id"]}


def _feedback_admin(request: Request, change=False):
    """Rueckmeldungen sieht nur der Oberadmin – je App die eigenen; alle anderen 404."""
    if _scope(request.state.user) != "all":
        raise HTTPException(404, "Nicht gefunden.")
    return _admin(request, change)


@core.get("/api/admin/feedback")
def api_admin_feedback(request: Request):
    _feedback_admin(request)
    users = auth.users()
    out, dc = [], devcosts.load()
    for x in feedback.items(auth_app(request)):
        e = {**x, "mail": (users.get(x["user"]) or {}).get("mail")}
        t = devcosts.ticket_fuer(x["id"], dc) if dc else None   # angelegtes Issue geht vor der Vormerkung
        if t:
            e["ticket"] = t
        out.append(e)
    return out


@core.patch("/api/admin/feedback/{fid}")
async def api_admin_feedback_done(fid: str, request: Request):
    _feedback_admin(request, change=True)
    try:
        b = await request.json()
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    if not isinstance(b, dict) or not ({"done", "ticket"} & b.keys()):
        raise HTTPException(400, "Ungültige Anfrage.")
    if "ticket" in b and not isinstance(b["ticket"], bool):
        raise HTTPException(400, "Ungültige Anfrage.")
    app_ = auth_app(request)
    if "ticket" in b:
        if not b["ticket"] and devcosts.ticket_fuer(fid):
            raise HTTPException(409, "Zu dieser Rückmeldung gibt es schon ein Ticket.")
        if not feedback.set_ticket(app_, fid, b["ticket"]):
            raise HTTPException(404, "Nicht gefunden.")
    if "done" in b and not feedback.set_done(app_, fid, b["done"]):
        raise HTTPException(404, "Nicht gefunden.")
    return {"ok": True}


@core.delete("/api/admin/feedback/{fid}")
def api_admin_feedback_delete(fid: str, request: Request):
    """Eine erledigte Rueckmeldung loeschen (0.43.0) – offene erst abhaken (409)."""
    _feedback_admin(request, change=True)
    try:
        e = feedback.delete(auth_app(request), fid)
    except feedback.NotDone:
        raise HTTPException(409, "Nur erledigte Rückmeldungen lassen sich löschen – bitte erst abhaken.") from None
    if e is None:
        raise HTTPException(404, "Nicht gefunden.")
    access.info("%s loescht Rueckmeldung %s von %s (%s)", who(request), fid, e["user"], auth_app(request))
    return {"ok": True}


@core.post("/api/admin/feedback/erledigte-loeschen")
def api_admin_feedback_delete_done(request: Request):
    """Alle erledigten Rueckmeldungen dieser App loeschen (0.43.0) -> Anzahl."""
    _feedback_admin(request, change=True)
    n = feedback.delete_done(auth_app(request))
    access.info("%s loescht %d erledigte Rueckmeldung(en) (%s)", who(request), n, auth_app(request))
    return {"ok": True, "deleted": n}


def _costs_admin(request: Request, change=False):
    """Kostenuebersicht: nur Oberadmin auf dem Rig-Host – fuer alle anderen 404 (keine Auskunft)."""
    if not is_rig(request) or _scope(request.state.user) != "all":
        raise HTTPException(404, "Nicht gefunden.")
    return _admin(request, change)   # 403 nur noch fuer Oberadmins ohne 2FA/CSRF


@core.get("/api/admin/costs")
def api_admin_costs(request: Request, month: str | None = None):
    _costs_admin(request)
    cur = time.strftime("%Y-%m")
    if month is None:
        month = cur
    elif not _MONTH_RE.fullmatch(month):
        raise HTTPException(422, "Monat muss die Form JJJJ-MM haben.")
    data = usage.month(month)
    prices = costs.load_prices()
    apps = {n: u.get("apps", []) for n, u in auth.users().items()}
    months = sorted(set(usage.months()) | {cur}, reverse=True)
    return {"month": month, "since": data["since"], "months": months, "prices": prices,
            "scenarios": costs.compute(data["users"], apps, prices, month),
            "dev": devcosts.monat(month, prices["usd_eur"])}


@core.put("/api/admin/costs/prices")
async def api_admin_costs_prices(request: Request):
    me = _costs_admin(request, change=True)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "Preise müssen als JSON-Objekt gesendet werden.") from None
    try:
        saved = costs.save_prices(body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    admin.record(me, "Preise", "Preise geändert")
    access.info("%s aendert die Kostenpreise", me)
    return {"prices": saved}


@core.get("/api/admin/versions")
def api_admin_versions(request: Request):
    """Was gerade laeuft (Live-Werte) + ausfuehrliches Protokoll aller Versionen."""
    ober = _scope(_admin(request)) == "all"
    data = os.environ.get("DATA_DIR", "/data")
    try:
        with open(os.path.join(data, "app", "rignavi.json"), encoding="utf-8") as f:
            app_v = json.load(f).get("version", "?")
    except (OSError, ValueError):
        app_v = "?"
    live = _live_values()
    tar = os.environ.get("VALHALLA_TAR", "/valhalla-data/valhalla_tiles.tar")
    rig = is_rig(request)
    running = {"server": app_version(rig), "stand": STAMP, "app": app_v, "valhalla": live.get("valhalla", "?"),
               "strassen": time.strftime("%d.%m.%Y", time.localtime(os.path.getmtime(tar))) if os.path.exists(tar) else "?",
               "osm": live.get("osm_date", "?"), "regionen": live.get("regions", "?"), "python": live.get("python", "?")}
    if not rig:
        running.pop("app")
    if not ober:   # Gruppen-Admins nur App-Stand (+ RigNavi), keine Server-Interna
        running = {k: v for k, v in running.items() if k in ("server", "stand", "app")}
    mine = ("rig", "beide") if rig else ("ride", "beide")
    tag = "rig" if rig else "ride"
    # Punkte mit Kennzeichen "intern" (z. B. Admin-Meldungen) erscheinen in keinen Release-Notes
    rel = [{**r, "items": [it for it in r.get("items", []) if len(it) < 3 or it[2] in (tag, "both")]}
           for r in _releases() if r.get("app") in mine]
    return {"running": running, "releases": [r for r in rel if r["items"]]}


@core.get("/api/admin/users")
def api_admin_users(request: Request):
    return admin.list_users(_scope(_admin(request)), auth_app(request))


@core.post("/api/admin/users")
async def api_admin_create(request: Request):
    me = _admin(request, change=True)
    b = await request.json()
    scope = _scope(me)
    pw = _admin_call(admin.create, b.get("name"), b.get("mail", ""),
                     b.get("apps") or (["ride", "rig"] if scope == "all" else scope["apps"]), b.get("admin") or False,
                     scope=scope, group=b.get("group"), app=auth_app(request))
    name = (b.get("name") or "").strip().lower()
    access.info("%s legt Login %s an", me, name)
    grp = admin.read_groups().get(auth.users().get(name, {}).get("group") or "", {}).get("name")
    admin.record(me, name, "angelegt" + (f" in {grp}" if grp else ""))
    mail_ok = None
    if b.get("send_mail") and b.get("mail"):
        subject, body, app = _access_mail(name, pw, auth.users().get(name, {}).get("apps") or [], is_rig(request))
        mail_ok = await asyncio.to_thread(mailer.send, b["mail"], subject, body, app)
    return {"name": name, "password": pw, "mail_ok": mail_ok}


def _access_mail(name, pw, apps, rig):
    """Zugangsmail nach den Apps des neuen Logins (seit 0.15.2): nur Ride -> nur RideAnalyzer usw."""
    names = {"ride": "RideAnalyzer", "rig": "RigAnalyzer"}
    if len(apps) == 1:
        a = apps[0]
        subject, where, app = f"Dein Zugang zum {names[a]}", f"  Adresse:  {PUBLIC_URL[a == 'rig']}\n", a
    else:
        subject = "Dein Zugang zu RideAnalyzer und RigAnalyzer"
        where = f"  Motorrad:  {PUBLIC_URL[False]}\n  Womo/Gespann: {PUBLIC_URL[True]}\n"
        app = "rig" if rig else "ride"
    body = (f"Hallo {name},\n\nhier ist dein Zugang:\n\n{where}  Benutzer: {name}\n  Passwort: {pw}\n\n"
            "Das Passwort kannst du nach der Anmeldung unter „Mein Konto“ ändern.\n")
    return subject, body, app


@core.patch("/api/admin/users/{name}")
async def api_admin_update(name: str, request: Request):
    me = _admin(request, change=True)
    b = await request.json()
    f = {k: b[k] for k in ("mail", "apps", "admin", "group", "locked") if k in b}
    was_pending = bool(auth.users().get(name, {}).get("pending"))
    res = _admin_call(admin.update, name, _scope(me), auth_app(request), **f)
    if "mail" in f:   # Admin setzt die Mail sofort – ein offener Wechsel des Logins ist damit hinfaellig
        mailchange.drop(name)
    access.info("%s aendert Login %s: %s", me, name, ", ".join(sorted(f)))
    approved = was_pending and not res["pending"] and not res["locked"]
    admin.record(me, name, "freigeschaltet" if approved and list(f) == ["locked"] else _change_text(f, res))
    if approved:
        _tell_approved(name, is_rig(request))
    return res


@core.post("/api/admin/users/{name}/password")
async def api_admin_password(name: str, request: Request):
    me = _admin(request, change=True)
    b = await request.json()
    pw = _admin_call(admin.set_password, name, b.get("password") or None, scope=_scope(me), app=auth_app(request))
    access.info("%s setzt Passwort von %s", me, name)
    admin.record(me, name, "Passwort neu gesetzt")
    return {"name": name, "password": pw}


@core.delete("/api/admin/users/{name}")
def api_admin_delete(name: str, request: Request):
    me = _admin(request, change=True)
    _admin_call(admin.delete, name, scope=_scope(me), app=auth_app(request))
    access.info("%s loescht Login %s", me, name)
    # auch seine Daten (Touren, Pakete, Einstellungen, Historie, Einladungen) – sonst erbt sie, wer den
    # Namen spaeter neu bekommt. Bewertungen/Rueckmeldungen/Verbrauch bleiben als „<name> (gelöscht)“.
    weg = purge.purge_user(name)
    admin.record(me, name, "gelöscht" + (f" ({', '.join(weg)} aufgeräumt)" if weg else ""))
    return Response(status_code=204)


def _change_text(f, res):
    apps = {"ride": "Ride", "rig": "Rig"}
    bits = []
    if "mail" in f:
        bits.append(f"Mail {res['mail'] or 'entfernt'}")
    if "apps" in f:
        bits.append("Apps " + "+".join(apps[a] for a in res["apps"]))
    if "group" in f:
        bits.append("Gruppe " + admin.read_groups().get(res["group"] or "", {}).get("name", "keine"))
    if "admin" in f:
        bits.append("Rechte: " + admin.rights_text(res["admin"]))
    if "locked" in f:
        bits.append("gesperrt" if res["locked"] else "entsperrt")
    return ", ".join(bits)


@core.get("/api/admin/report")
def api_admin_report(request: Request, day: str = ""):
    """Vorschau des Tagesberichts (Standard: gestern) – nur Oberadmin."""
    import datetime as _dt
    me = _admin(request)
    if _scope(me) != "all":
        raise HTTPException(403, "Der Tagesbericht ist nur für den Oberadmin.")
    try:
        d = _dt.date.fromisoformat(day) if day else _dt.date.today() - _dt.timedelta(days=1)
    except ValueError:
        raise HTTPException(422, "Datum als JJJJ-MM-TT.") from None
    built = report.build(d, events.read_day(d), _report_health(), _report_space())
    text = f"Betreff: {built[0]}\n\n{built[1]}" if built else f"{d.strftime('%d.%m.%Y')}: keine Nutzung, keine Fehler – an solchen Tagen kommt keine Mail."
    return Response(text, media_type="text/plain; charset=utf-8")


@core.get("/api/admin/log")
def api_admin_log(request: Request):
    me = _admin(request)
    return admin.log_for(me, _scope(me), auth_app(request))[:100]


@core.get("/api/admin/groups")
def api_admin_groups(request: Request):
    return admin.list_groups(_scope(_admin(request)))


def _oberadmin(request: Request):
    me = _admin(request, change=True)
    if _scope(me) != "all":
        raise HTTPException(403, "Gruppen verwaltet nur der Oberadmin.")
    return me


@core.get("/api/admin/newsletter")
def api_admin_newsletter(request: Request):
    """Newsletter (0.50.0, Ticket #16): wie viele wollen was, bisher verschickte."""
    _news_admin(request)
    app = auth_app(request)
    return {"releases": len(notify.recipients(app, "releases")), "newsletter": len(notify.recipients(app, "newsletter")),
            "log": notify.newsletter_log(app)[:10]}


_NEWSLETTER_BODY = '''
<div class="nl-grid">
<section class="card nl-edit">
  <form id="nl-form" class="lg-form" autocomplete="off">
    <label>Betreff<input name="subject" id="nl-subject" maxlength="120" required></label>
    <div class="nl-tools" role="toolbar" aria-label="Formatieren">
      <button type="button" class="ghost small" data-md="h1" title="Überschrift"># Titel</button>
      <button type="button" class="ghost small" data-md="h2" title="Zwischenüberschrift">## Unter</button>
      <button type="button" class="ghost small" data-md="b" title="fett"><b>F</b></button>
      <button type="button" class="ghost small" data-md="i" title="kursiv"><i>K</i></button>
      <button type="button" class="ghost small" data-md="ul" title="Liste">• Liste</button>
      <button type="button" class="ghost small" data-md="a" title="Link">🔗 Link</button>
    </div>
    <label>Text<textarea name="text" id="nl-text" rows="18" maxlength="20000" required
      placeholder="# Neu im Frühjahr&#10;&#10;Hallo zusammen,&#10;&#10;- **Maut in Euro** für Tunnel und Brücken&#10;- [Häufige Fragen](https://…)"></textarea></label>
    <p class="hint">Formatierung: <code># Überschrift</code>, <code>## Unterüberschrift</code>, <code>- Liste</code>, <code>**fett**</code>,
    <code>*kursiv*</code>, <code>[Text](https://…)</code>. Abmeldelink und Absender (noreply) kommen automatisch dazu.
    <span id="nl-draft-state"></span></p>
    <div class="nl-actions">
      <button type="button" class="ghost" id="nl-test">✉️ Testmail an mich</button>
      <button type="submit" class="primary" id="nl-send">An alle mit Häkchen schicken</button>
      <button type="button" class="linkish" id="nl-clear">Leeren</button>
    </div>
    <p id="nl-msg" hidden></p>
  </form>
</section>
<section class="card nl-prev">
  <div class="kt-top"><h3 style="margin:0">Vorschau</h3><span class="hint" id="nl-rcpt"></span></div>
  <div id="nl-preview" class="nl-preview"><p class="hint">Hier erscheint die Mail, wie sie ankommt.</p></div>
</section>
</div>
<section class="card">
  <h3 style="margin-top:0">Bisher verschickt</h3>
  <ul class="kt-liste" id="nl-log"><li class="hint">lädt …</li></ul>
</section>
<p><a href="/admin">← zurück zum Admin-Bereich</a></p>'''


@core.get("/admin/newsletter")
def admin_newsletter_page(request: Request):
    """Newsletter-Editor (0.50.0, Ticket #16): nur Oberadmin mit 2FA."""
    if not auth.is_admin(request.state.user) or request.state.via != "cookie" or _scope(request.state.user) != "all":
        raise HTTPException(404, "Nicht gefunden.")
    if not twofa.enabled(request.state.user):
        return RedirectResponse("/2fa-einrichten", status_code=303)
    if not twofa.mfa_ok(request, request.state.user):
        return RedirectResponse("/zweiter-faktor?next=/admin/newsletter", status_code=303)
    return HTMLResponse(_page(is_rig(request), "Admin", "Newsletter schreiben: an alle, die ihn unter Mein Konto bestellt haben.",
                              _NEWSLETTER_BODY, scripts=("newsletter-editor.js",)), headers={"Cache-Control": "no-store"})


def _news_admin(request: Request, change=False):
    me = _admin(request, change=change)
    if _scope(me) != "all":
        raise HTTPException(403, "Newsletter verschickt nur der Oberadmin.")
    return me


@core.get("/api/admin/newsletter/draft")
def api_admin_newsletter_draft(request: Request):
    _news_admin(request)
    return notify.draft(auth_app(request))


@core.put("/api/admin/newsletter/draft")
async def api_admin_newsletter_draft_put(request: Request):
    me = _news_admin(request, change=True)
    b = await request.json()
    return await asyncio.to_thread(notify.save_draft, auth_app(request), b.get("subject"), b.get("text"), me)


@core.post("/api/admin/newsletter/preview")
async def api_admin_newsletter_preview(request: Request):
    _news_admin(request, change=True)
    b = await request.json()
    app = auth_app(request)
    return notify.preview(app, str(b.get("subject") or "")[:notify.SUBJECT_MAX], str(b.get("text") or "")[:notify.TEXT_MAX],
                          PUBLIC_URL[app == "rig"])


@core.post("/api/admin/newsletter")
async def api_admin_newsletter_send(request: Request):
    me = _oberadmin(request)
    b = await request.json()
    app = auth_app(request)
    test_to = None
    if b.get("test"):
        mail = auth.users().get(me, {}).get("mail")
        if not mail:
            raise HTTPException(422, "Für die Testmail fehlt deine eigene Mailadresse (Mein Konto).")
        test_to = (me, mail)
    try:
        res = await asyncio.to_thread(notify.newsletter, app, b.get("subject"), b.get("text"), PUBLIC_URL[app == "rig"], me, test_to)
    except notify.NewsError as e:
        raise HTTPException(422, str(e)) from None
    access.info("%s verschickt Newsletter%s an %d", me, " (Test)" if test_to else "", res["total"])
    if not test_to:
        notify.clear_draft(app)
    return res


@core.post("/api/admin/groups")
async def api_admin_group_create(request: Request):
    me = _oberadmin(request)
    b = await request.json()
    g = _admin_call(admin.create_group, b.get("name"))
    access.info("%s legt Gruppe %s an", me, g["name"])
    admin.record(me, g["name"], "Gruppe angelegt")
    return g


@core.patch("/api/admin/groups/{gid}")
async def api_admin_group_rename(gid: str, request: Request):
    me = _oberadmin(request)
    b = await request.json()
    old = admin.read_groups().get(gid, {}).get("name", gid)
    g = _admin_call(admin.rename_group, gid, b.get("name"))
    admin.record(me, g["name"], f"Gruppe umbenannt (vorher {old})")
    access.info("%s benennt Gruppe %s um in %s", me, gid, g["name"])
    return g


@core.delete("/api/admin/groups/{gid}")
def api_admin_group_delete(gid: str, request: Request):
    me = _oberadmin(request)
    old = admin.read_groups().get(gid, {}).get("name", gid)
    _admin_call(admin.delete_group, gid)
    admin.record(me, old, "Gruppe gelöscht")
    access.info("%s loescht Gruppe %s", me, gid)
    return Response(status_code=204)


@core.get("/api/ready")
def api_ready():
    """Fuer den Docker-Healthcheck: bereit erst, wenn die Beispiele vorberechnet sind.
    Bewusst 425 statt 503 – 503 faengt Traefik als Wartungsseite ab."""
    body = {"ready": warm_state["done"], "beispiele_offen": warm_state["todo"], "gerade": warm_state["current"]}
    return JSONResponse(body, status_code=200 if warm_state["done"] else 425)


@core.get("/api/plan/status")
def api_plan_status():
    return planner.status()

@core.post("/api/demo/{name}")
def api_demo(name: str, request: Request):
    path = os.path.join(HERE, "static", "demo", f"{name}.gpx")
    if not name.isalnum() or not os.path.isfile(path):
        raise HTTPException(404, "Unbekannte Beispieltour.")
    rig = is_rig(request)
    cached = _demo_load(name, rig)
    if cached is not None:
        job_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[job_id] = {"state": "done", "progress": 100, "msg": "fertig (vorberechnet)", "t": time.time(),
                            "by": who(request), "result": cached}
        access.info("%s Beispiel %s (%s) aus dem Vorrat", who(request), name, "rig" if rig else "ride")
        events.log("beispiel", request.state.user, "rig" if rig else "ride", name=name)
        return {"job": job_id}
    with open(path, "rb") as f:
        job = _submit(f.read(), f"{name}.gpx", {"rig": rig}, who(request), cache=False)
    jobs[job["job"]]["demo"] = (name, rig)
    return job


@core.get("/api/jobs/{job_id}")
def api_job(job_id: str, request: Request):
    j = jobs.get(job_id)
    # nur der eigene Job (oder Moderatoren wie beim Abbrechen); fremde sind wie nicht vorhanden
    if j and j.get("by", "-").split("@")[0] != request.state.user and not _may_moderate(request, request.state.user):
        j = None
    if not j:
        raise HTTPException(404, "Die Analyse ist weg – der Server wurde zwischendurch aktualisiert oder neu gestartet. Bitte die Route nochmal analysieren.")
    out = {k: v for k, v in j.items() if k not in ("t", "by", "demo", "cache_key", "seq", "est", "km")}
    if "from_cache" in out:
        out["from_cache"] = True
    if j["state"] == "queued":   # Platz + Restzeit statt „wartet“ – auf einer Kopie (nicht ohne Sperre iterieren)
        with jobs_lock:
            snap = {k: dict(v) for k, v in jobs.items()}
        out["msg"] = jobqueue.wait_text(snap, job_id, {"lokal": MAX_JOBS, "kurz": SHORT_JOBS, "live": LIVE_JOBS}) or j["msg"]
    return JSONResponse(out)


@core.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str, request: Request):
    """Laufende Analyse abbrechen (0.28.0): nur die eigene (oder als Admin). Sie endet beim naechsten Schritt."""
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "Analyse nicht gefunden.")
    me = request.state.user
    if request.state.via == "cookie" and not auth.csrf_ok(request):
        raise HTTPException(403, "Bitte die Seite neu laden und erneut versuchen.")
    if j.get("by", "-").split("@")[0] != me and not _may_moderate(request, me):
        raise HTTPException(403, "Nur eigene Analysen.")
    if j["state"] in ("queued", "running"):
        j["cancel"] = True
        access.info("%s bricht Analyse %s ab", who(request), job_id[:8])
    return {"state": j["state"], "cancel": bool(j.get("cancel"))}


def all_regions():
    """Konfigurierte (Ansible) + auf der Seite nachbestellte Regionen."""
    return OSM_REGIONS + [r for r in regions.extra_regions() if r not in OSM_REGIONS]


def queue_eta():
    """Warteschlange mit geschaetzter Fertigstellung: [{region, name, eta (epoch), building}]."""
    legacy = _legacy_regions()
    out, t = [], time.time()
    for r in all_regions():
        if not _region_due(r, legacy):
            continue
        est = regions.est_seconds(r) or 3600
        if r == db_state["building"] and db_state["started"]:
            t = max(t, db_state["started"] + est)
        else:
            t += est
        out.append({"region": r, "name": regions.name_of(r), "eta": round(t), "building": r == db_state["building"]})
    return out


def _missing_with_eta(missing):
    q = {e["region"]: e for e in queue_eta()}
    out = []
    for m in missing:
        m = dict(m)
        if m.get("region"):
            m["size_mb"] = regions.size_mb(m["region"])
            est = regions.est_seconds(m["region"])
            m["est_min"] = None if est is None else round(est / 60)
            if m["region"] in q:
                m["queued"] = True
                m["eta"] = q[m["region"]]["eta"]
        out.append(m)
    return out


@core.get("/api/info")
def api_info(request: Request):
    db = osmdb.get()
    have = db.regions if db else []
    return {
        "version": app_version(is_rig(request)), "stamp": STAMP, "stage": STAGE, "git": GIT_REF or None,
        "osm": None if db is None else {"regions": have, "built": db.meta.get("built"),
                                         "names": {r: regions.name_of(r) for r in have},
                                         "countries": regions.by_country(have)},
        "osm_building": db_state["building"], "osm_error": db_state["last_error"],
        "osm_pending": [r for r in all_regions() if _region_due(r)],
        "queue": queue_eta(),
        "max_mb": MAX_BYTES // 1024 // 1024, "max_km": analyze.MAX_KM,
        **({"lez": {k: (lez.meta() or {}).get(k) for k in ("bnzfe", "osm")}} if is_rig(request) else {}),
    }


_cov_cache = {"key": None, "data": None}
# Atlantik-Inseln (Azoren, Madeira) stecken im Portugal-Auszug, gehoeren aber nicht auf die Karte
ATLANTIC = (-35.0, 27.0, -10.0, 45.0)


@core.get("/api/coverage")
def api_coverage():
    """GeoJSON: lokal vorhandene Regionen (Umriss aus der DB) + Regionen in Arbeit (Katalog)."""
    import shapely
    from shapely.geometry import mapping
    db = osmdb.get()
    q = queue_eta()
    key = (tuple(sorted((r, d.meta.get("built")) for d in (db.dbs if db else []) for r in d.regions)),
           tuple((e["region"], e["building"]) for e in q))
    if _cov_cache["key"] == key:
        return JSONResponse(_cov_cache["data"], headers={"Cache-Control": "no-cache"})
    feats = []
    for d in (db.dbs if db else []):
        g = shapely.simplify(d.coverage.difference(shapely.box(*ATLANTIC)), 0.01, preserve_topology=True)
        for r in d.regions:
            feats.append({"type": "Feature", "geometry": mapping(g),
                          "properties": {"region": r, "name": regions.name_of(r), "status": "lokal",
                                         "built": (d.meta.get("built") or "")[:10]}})
    idx = {r["region"]: r for r in (regions._load_index(download=False) or [])}
    for e in q:
        r = idx.get(e["region"])
        if not r:
            continue
        g = shapely.simplify(r["geom"], 0.01, preserve_topology=True)
        feats.append({"type": "Feature", "geometry": mapping(g),
                      "properties": {"region": e["region"], "name": e["name"],
                                     "status": "im Bau" if e["building"] else "geplant", "eta": e["eta"]}})
    data = {"type": "FeatureCollection", "features": feats}
    _cov_cache.update(key=key, data=data)
    return JSONResponse(data, headers={"Cache-Control": "no-cache"})


@core.post("/api/regions")
async def api_regions(request: Request):
    # nur Oberadmins (mit 2FA) – jede Region kostet Stunden Bau und bis 1,2 GB, die 12 Plaetze waren sonst fuer
    # den ersten beliebigen Login weg. Alle anderen bekommen einen Hinweis statt des Fehlers.
    me = request.state.user
    if not auth.is_admin(me) or request.state.via != "cookie" or _scope(me) != "all" or not twofa.mfa_ok(request, me):
        raise HTTPException(403, "Neue Regionen nimmt Richy auf – schick ihm einfach eine Rückmeldung mit der Region.")
    if os.environ.get("OSM_AUTOBUILD", "1") != "1":
        # Kartendaten baut der Baurechner, die App liest sie nur.
        raise HTTPException(409, "Diese App baut keine Kartendaten mehr. Neue Regionen in plan-regionen.yml eintragen – "
                                 "der Baurechner nimmt sie beim nächsten Bau auf.")
    body = await request.json()
    region = str(body.get("region", ""))[:120]
    db = osmdb.get()
    ok, msg = regions.request(region, OSM_REGIONS, db.regions if db else [])
    log.info("Region %s angefragt von %s: %s", region, who(request), msg)
    if not ok:
        raise HTTPException(400, msg)
    wake.set()
    q = {e["region"]: e for e in queue_eta()}
    return {"ok": True, "msg": msg, "eta": q.get(region, {}).get("eta"), "name": regions.name_of(region)}


# Strenge CSP direkt aus der App (alles lokal, nur Kacheln von OSM). Inline-Styles
# erlaubt: Balken/Farben werden per style-Attribut gesetzt.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: https://tile.openstreetmap.org https://tilecache.rainviewer.com; "
       # Luftbild (Esri) und Einfahrt-Fotos (Mapillary/fbcdn) kommen ueber den eigenen Server, nicht direkt
       "connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


# Ohne Anmeldung erreichbar: Login-Seite und was sie zum Aussehen braucht
PUBLIC = {"/login", "/registrieren", "/info.html", "/datenschutz", "/datenschutz.html", "/impressum", "/impressum.html",
          "/logout", "/passwort-vergessen", "/passwort-neu", "/mail-bestaetigen", "/abbestellen",
          "/zweiter-faktor", "/api/ready",
          "/favicon.ico", "/static/theme.js", "/static-ride/style.css",
          "/static-rig/rig.css", "/static/pages.css", "/static/v2.css", "/static/pwtoggle.js", "/static-ride/icon.svg", "/static-rig/icon.svg"}


def auth_app(request: Request) -> str:
    return "rig" if is_rig(request) else "ride"


async def headers(request: Request, call_next):
    path = request.url.path
    a = auth_app(request)
    # Basic-Auth nur fuer Programme (RigNavi = okhttp, curl ...), nicht fuer Browser: die schicken
    # ein frueher gemerktes Passwort sonst ewig mit, und "Abmelden" haette keine Wirkung.
    browser = "mozilla" in (request.headers.get("user-agent") or "").lower()
    request.state.user = auth.read_cookie(request.cookies.get(auth.COOKIE, ""), a)
    request.state.via = "cookie" if request.state.user else None
    if request.state.user and not path.startswith(("/static", "/vendor")):   # angemeldet zurueck (Cookie, 30 Tage)
        loginwatch.app_seen(request.state.user, a, _client_ip(request), "browser")
    hdr = request.headers.get("authorization") or ""
    if not request.state.user and not browser and hdr.lower().startswith("basic "):
        ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "?")
        if auth.throttled(ip):   # Durchprobieren per Basic-Auth genauso bremsen wie die Login-Seite
            access.info("-@%s Basic-Auth gesperrt (zu viele Fehlversuche)", ip)
            return JSONResponse({"detail": "Zu viele Fehlversuche – bitte in 15 Minuten nochmal."}, status_code=429)
        request.state.user = await asyncio.to_thread(auth.basic_user, hdr, a)   # bcrypt blockiert sonst alle
        request.state.via = "basic" if request.state.user else None
        if request.state.user:
            auth.release(ip)
            loginwatch.app_seen(request.state.user, a, ip, request.headers.get("user-agent"))
        else:
            auth.failed(ip)
    if request.state.user and not path.startswith(("/static", "/vendor")):
        usage.seen(request.state.user, a)   # aktiver Tag (je Tag und Login nur ein Schreibzugriff)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # CSRF zentral statt je Endpunkt. HTML-Formulare (ohne JS) pruefen Origin/Referer, alles andere
        # mit Browser-Sitzung braucht das Double-Submit-Token (theme.js haengt es an jedes fetch()). RigNavi/Skripte
        # mit Basic-Auth sind nicht betroffen – das schickt kein Browser von allein mit.
        if path in FORM_POSTS:
            # Formulare mit Geheimnis im Formular (Token aus der Mail) brauchen keine Herkunftsangabe: der Reverse-Proxy setzt
            # Referrer-Policy no-referrer, der Browser schickt dann „Origin: null“ ohne Referer.
            # Eine erkennbar fremde Herkunft bleibt gesperrt.
            need = request.state.via == "cookie" and path not in TOKEN_FORMS
            bad = not auth.same_origin(request, require=need)
        else:
            bad = request.state.via == "cookie" and not auth.csrf_ok(request)
        if bad:
            access.info("%s %s %s -> CSRF-Pruefung fehlgeschlagen", who(request), request.method, path)
            resp = JSONResponse({"detail": "Sicherheitsprüfung fehlgeschlagen – bitte die Seite neu laden."}, status_code=403)
            resp.headers["Content-Security-Policy"] = CSP
            return resp
    if (request.state.via == "cookie" and auth.is_admin(request.state.user) and not twofa.enabled(request.state.user)
            and path not in PUBLIC and path != "/2fa-einrichten" and not path.startswith(("/static", "/vendor"))):
        # Admins ohne zweiten Faktor richten ihn ein, bevor sie irgendetwas anderes tun
        if path.startswith("/api/") or request.method != "GET":
            resp = JSONResponse({"detail": "Erst den zweiten Faktor einrichten (/2fa-einrichten)."}, status_code=403)
        else:
            resp = RedirectResponse("/2fa-einrichten", status_code=303)
        resp.headers["Content-Security-Policy"] = CSP
        return resp
    if not request.state.user and path not in PUBLIC:
        if path.startswith("/api/") or request.method != "GET":
            resp = JSONResponse({"detail": "Bitte anmelden."}, status_code=401)
        else:
            from urllib.parse import quote
            nxt = path + (("?" + request.url.query) if request.url.query else "")
            resp = RedirectResponse("/login" + ("" if nxt == "/" else "?next=" + quote(nxt)), status_code=303)
        resp.headers["Content-Security-Policy"] = CSP
        return resp
    try:
        resp = await call_next(request)
    except RuntimeError as e:
        # Browser hat die Anfrage abgebrochen, bevor die Antwort fertig war (Seite neu geladen, Tab im Hintergrund):
        # kein Fehler der App – nur kurz notieren statt Traceback
        if str(e) != "No response returned.":
            raise
        access.info("%s %s %s -> abgebrochen vom Client", who(request), request.method, path)
        return Response(status_code=499)
    path = request.url.path
    if request.state.user and not path.startswith("/static") and path not in ("/api/nav/ping", "/api/admin/activity"):
        ua = (request.headers.get("user-agent") or "").lower()
        activity.touch(request.state.user, _client_ip(request), "RigNavi" if "okhttp" in ua else "Browser",
                       request.method, path)
    if not (path.startswith("/static") or path.startswith("/api/jobs/") or path in ("/api/info", "/api/coverage")):
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(".")[0]
        access.info("%s %s %s %s -> %s", who(request), host, request.method, path, resp.status_code)
    if request.state.via == "cookie" and not request.cookies.get(auth.CSRF_COOKIE):
        # aeltere Anmeldungen bekommen das CSRF-Cookie nachgereicht
        resp.set_cookie(auth.CSRF_COOKIE, auth.new_csrf(), httponly=False, secure=True, samesite="strict", path="/")
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static/") and "Cache-Control" not in resp.headers:
        # immer kurz nachfragen (304 ist billig) – sonst cacht Firefox heuristisch alte Skripte
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# Klassische HTML-Formulare (POST ohne JS): fuer sie gilt die Origin-/Referer-Pruefung statt des CSRF-Tokens
FORM_POSTS = {"/abbestellen", "/login", "/logout", "/registrieren", "/passwort-vergessen", "/passwort-neu", "/mail-bestaetigen", "/zweiter-faktor",
              "/2fa-einrichten"}
TOKEN_FORMS = {"/mail-bestaetigen", "/passwort-neu", "/abbestellen"}   # tragen den Token aus der Mail im Formular

# Obergrenze fuer Anfrage-Koerper, aeusserste Schicht (zuletzt registriert). Ohne Anmeldung: 64 KB;
# GPX-Upload 5 MB (+ Formular-Rahmen); gespeicherte Analysen bis trips.MAX_ANALYSIS.
import bodylimit  # noqa: E402
def create_app(profile="ride"):
    """App zusammensetzen (seit 0.46.7, Trennung Ride/Rig): Kern-Router + App-Router der Erweiterungen fuer diese App
    (seit 0.46.8 je App: hooks "app.router.<app>", z. B. app.router.rig).
    Reihenfolge der Schichten wie vorher (innen -> aussen): QA-Band, GZip, headers (Anmeldung, CSRF, CSP), BodyLimit."""
    app = FastAPI(title="RideAnalyzer", docs_url=None, redoc_url=None, openapi_url=None)
    # Stage: prod | qa – QA zeigt auf jeder HTML-Seite ein gelbes Band (stage.py). Vor GZip registriert
    # = innen: das Band sieht den unkomprimierten Text.
    if STAGE == "qa":
        import stage
        app.add_middleware(stage.QaBanner, version=VERSION, git_ref=GIT_REF)
    # Reise-Ergebnisse (viele Etappen) werden schnell mehrere MB JSON -> komprimieren
    app.add_middleware(GZipMiddleware, minimum_size=2000)
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    app.mount("/static-ride", StaticFiles(directory=os.path.join(HERE, "static-ride")), name="static-ride")
    app.mount("/static-rig", StaticFiles(directory=os.path.join(HERE, "static-rig")), name="static-rig")
    app.include_router(core)
    for router in hooks.all_(f"app.router.{profile}"):   # App-Router (Rig: rig_routes.rig, angemeldet in rig_ext.py)
        app.include_router(router)
    app.middleware("http")(headers)
    large = hooks.one(f"app.bodylimit_large.{profile}") or {}   # Rig: gespeicherte Analysen (trips.MAX_ANALYSIS)
    app.add_middleware(bodylimit.BodyLimit, default=MAX_BYTES + 1024 * 1024, small=64 * 1024, small_paths=PUBLIC | FORM_POSTS,
                       large=large.get("size", 0), large_prefix=large.get("prefix", ()), large_suffix=large.get("suffix", ()))
    app.add_middleware(web.ProfileScope, profile=profile)   # aeusserste Schicht: diese App ist <profile>
    return app


# RA_APP=ride|rig -> nur diese App (eigener Container); sonst beide hinter dem Host-Verteiler
app = create_app(web.single_app()) if web.single_app() else web.HostDispatch({a: create_app(a) for a in web.APPS})


# ---------------------------------------------------------------- OSM-DB pflegen

def _age(path):
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def region_file(region):
    return os.path.join(OSM_DIR, region.replace("/", "_") + ".sqlite")


def _legacy_regions():
    """Regionen der alten Sammel-DB, solange sie noch nicht faellig ist."""
    age = _age(OSM_DB)
    if age is None or age > OSM_MAX_AGE:
        return set()
    try:
        con = sqlite3.connect(f"file:{OSM_DB}?mode=ro", uri=True)
        try:
            return set(json.loads(con.execute("SELECT v FROM meta WHERE k='regions'").fetchone()[0]))
        finally:
            con.close()
    except Exception:  # noqa: BLE001 – DB fehlt/kaputt: leere Menge statt Absturz
        return set()


_schema_cache = {}


def _schema_of(path):
    """DB-Schema einer Regionsdatei (meta 'schema'), gecacht je Aenderungszeit."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return 0
    if key not in _schema_cache:
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            row = con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
            con.close()
            _schema_cache[key] = int(row[0]) if row and str(row[0]).isdigit() else 1
        except Exception:  # noqa: BLE001 – DB fehlt/kaputt: Schema 0 statt Absturz
            _schema_cache[key] = 0
    return _schema_cache[key]


def _region_due(region, legacy=None):
    path = region_file(region)
    age = _age(path)
    if age is not None:
        # zu alt ODER mit aelterem Schema gebaut (neue Inhalte, z. B. Hoehen/Gewichte fuer RigAnalyzer)
        return age > OSM_MAX_AGE or _schema_of(path) < build_db.SCHEMA_VERSION
    return region not in (legacy if legacy is not None else _legacy_regions())


def _maintain_db():
    """Haelt je Region eine DB-Datei aktuell (erster Start, dann alle OSM_MAX_AGE_DAYS).
    Jede Region wird fuer sich gebaut und atomar ersetzt: ein Neustart des Containers
    kostet hoechstens die gerade laufende Region, fertige bleiben sofort nutzbar.
    Eigener Prozess (nice 10), damit der Webserver reaktionsfaehig bleibt."""
    time.sleep(20)
    os.makedirs(OSM_DIR, exist_ok=True)
    while True:
        wake.clear()
        legacy = _legacy_regions()
        failed = set()
        for region in all_regions():
            if region in failed or not _region_due(region, legacy):
                continue
            db_state.update(building=region, started=time.time())
            log.info("Baue OSM-DB fuer %s ...", region)
            try:
                r = subprocess.run(["nice", "-n", "10", sys.executable,  # noqa: S603,S607 – feste Argumente, Region aus der Konfiguration os.path.join(HERE, "build_db.py"),
                                    region_file(region), region], capture_output=True, text=True, timeout=6 * 3600)
                if r.returncode != 0:
                    db_state["last_error"] = f"{region}: " + (r.stderr or r.stdout)[-500:]
                    log.error("OSM-DB-Build %s fehlgeschlagen: %s", region, db_state["last_error"])
                    failed.add(region)
                else:
                    db_state["last_error"] = None
                    regions.record_build(region, time.time() - db_state["started"])
                    log.info("OSM-DB %s fertig. %s", region,
                             r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "")
            except Exception as e:
                db_state["last_error"] = f"{region}: {e}"
                log.exception("OSM-DB-Build %s abgebrochen", region)
                failed.add(region)
            db_state["building"] = None
        # Alte Sammel-DB weg, sobald alle ihre Regionen eigene Dateien haben
        if os.path.exists(OSM_DB) and all(_age(region_file(r)) is not None for r in OSM_REGIONS):
            log.info("Alte Sammel-DB %s ersetzt, wird entfernt", OSM_DB)
            os.remove(OSM_DB)
        # alle 6 h nachsehen (Monats-Update); bestellte Region weckt sofort. Fehlgeschlagene
        # Regionen erst im naechsten Durchlauf erneut – nicht im Minutentakt bei Geofabrik.
        wake.wait(6 * 3600)


if os.environ.get("OSM_AUTOBUILD", "1") == "1":
    threading.Thread(target=_maintain_db, daemon=True, name="osm-db").start()


def _lez_loop():
    """Umweltzonen (lez.py): beim Start und danach taeglich nachsehen, abrufen wenn aelter als 7 Tage."""
    time.sleep(30)
    while True:
        try:
            if lez.due(lez.meta()):
                lez.refresh()
        except Exception:
            log.exception("Umweltzonen-Abruf abgebrochen")
        time.sleep(24 * 3600)


if os.environ.get("LEZ_REFRESH", "1") == "1":
    threading.Thread(target=_lez_loop, daemon=True, name="lez").start()


# Tagesbericht-Thread erst hier am Modulende starten: er ruft u. a. _live_values() auf, das weiter oben
# erst spaeter definiert wird.
if os.environ.get("REPORT", "1") == "1":
    threading.Thread(target=_report_loop, daemon=True, name="tagesbericht").start()

if os.environ.get("NEWS_DIGEST", "1") == "1":
    for _a in ([os.environ["RA_APP"].strip()] if os.environ.get("RA_APP", "").strip() in ("ride", "rig") else ["ride", "rig"]):
        threading.Thread(target=notify.digest_loop, args=(_a, _releases, VERSION, PUBLIC_URL[_a == "rig"]),
                         daemon=True, name=f"sammelmail-{_a}").start()
