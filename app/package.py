"""Reisepaket fuer RigNavi (seit v0.9): alles, was das Handy braucht, um eine Reise offline zu fahren.

  strassen.tar   Valhalla-Kacheln im Streifen ±25 km (ohne index.bin)
  karte.pmtiles  Kartenstreifen ±10 km aus dem Protomaps-Build
  stil/          Kartenstil hell/dunkel, Schriften, Symbole
  reise.json     Etappen, Gespann-Warnungen, Tankstellen/Entsorgung/Stellplaetze
  paket.json     Steckbrief mit Pruefsummen – wird ZULETZT geschrieben, erst dann gilt das Paket

Ablage data/packages/<benutzer>/<reise>/<zeitstempel>/, gebaut in <zeitstempel>.tmp und erst fertig
umbenannt; ein fehlgeschlagener Bau laesst das vorige Paket stehen. Ein Bau gleichzeitig (Warteschlange).
"""

import datetime
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable

import corridor
import reisedaten
import trips
import usage

log = logging.getLogger("package")

FORMAT = 1
ROADS_KM, OUTLINE_KM, MAP_KM = 25, 20, 10
# der Server teilt die Platte mit anderen Diensten – vor jedem Bau so viel frei lassen
MIN_FREE_BYTES = int(float(os.environ.get("PACKAGE_MIN_FREE_GB", "8")) * 1e9)
MAX_PACKAGES_PER_USER = int(os.environ.get("PACKAGE_MAX_PER_USER", "2"))
VALHALLA_TAR = os.environ.get("VALHALLA_TAR", "/valhalla-data/valhalla_tiles.tar")


@dataclass
class Steps:
    plan: Callable[[dict], dict]            # Reise -> planner.plan-Ergebnis (gpx, stages, ...)
    roads: Callable[[str, list], dict]      # Ziel-tar, Routenpunkte -> {"tiles", "bytes"}
    map: Callable[[str, list], str]         # Ziel-pmtiles, Umriss-Ringe (aussen + Loecher) -> Karten-Build
    style: Callable[[str], None]            # Paketordner -> legt stil/ an
    analyze: Callable[[bytes], dict]        # GPX -> Analyse-Ergebnis (Rig)
    roads_date: Callable[[], str]           # Stand der Strassendaten


def _base(user, trip_id):
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "packages", user.strip().lower(), trip_id)


def _status_path(user, trip_id):
    return os.path.join(_base(user, trip_id), "status.json")


_status_lock = threading.Lock()

# Bau-Schritte: (Text waehrend, Text fertig, Prozent beim Start) – die Webseite zeigt daraus die Schrittliste
STEPS = (("Route wird berechnet", "Route berechnet", 5),
         ("Straßendaten werden ausgeschnitten", "Straßendaten ausgeschnitten", 20),
         ("Karte wird geladen", "Karte geladen", 40),
         ("Gespann-Prüfung läuft", "Gespann geprüft", 70),
         ("Paket wird abgeschlossen", "Paket abgeschlossen", 95))


def _set_status(user, trip_id, **st):
    with _status_lock:   # Anfrage-Thread und Bau-Thread schreiben dieselbe Datei
        os.makedirs(_base(user, trip_id), exist_ok=True)
        path = _status_path(user, trip_id)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".status")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, path)


def current_dir(user, trip_id):
    base = _base(user, trip_id)
    if not re.match(trips.ID_RE, trip_id or "") or not os.path.isdir(base):
        return None
    done = sorted(n for n in os.listdir(base)
                  if not n.endswith(".tmp") and os.path.isfile(os.path.join(base, n, "paket.json")))
    return os.path.join(base, done[-1]) if done else None


def manifest(user, trip_id):
    d = current_dir(user, trip_id)
    if not d:
        return None
    with open(os.path.join(d, "paket.json"), encoding="utf-8") as f:
        return json.load(f)


def status(user, trip_id):
    st = {"state": "none", "msg": "", "built": None, "size": 0, "progress": 0, "step": None}
    if re.match(trips.ID_RE, trip_id or "") and os.path.isfile(_status_path(user, trip_id)):
        try:
            with open(_status_path(user, trip_id), encoding="utf-8") as f:
                st.update(json.load(f))
        except (OSError, ValueError):
            pass
    m = manifest(user, trip_id)
    if m:
        st["built"] = m["built"]
        st["size"] = sum(f["size"] for f in m["files"])
        if st["state"] in ("none", "ready"):
            st["state"] = "ready"
    st["steps"] = [{"doing": a, "done": b} for a, b, _ in STEPS]
    now = time.time()
    if st["state"] == "building" and st.get("started"):
        st["elapsed"] = max(0, int(now - st["started"]))
        st["eta"] = eta(st.get("step"), now - (st.get("step_at") or now), st.get("km"))
    if st["state"] == "queued":
        if st.get("queued_at"):
            st["waiting"] = max(0, int(now - st["queued_at"]))
        st["ahead"] = _ahead(user, trip_id)
    return st


TIMING_KEEP = 20   # so viele fertige Bauten merken (je Container: Prod und QA getrennt)


def _timing_path():
    # bewusst nicht unter packages/ – dort ist jeder Eintrag ein Benutzerordner (recover, _prune_user)
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "package-timing.json")


def _load_timing():
    try:
        with open(_timing_path(), encoding="utf-8") as f:
            data = json.load(f)
        return [x for x in data if isinstance(x, dict) and len(x.get("steps") or []) == len(STEPS)]
    except (OSError, ValueError, TypeError):
        return []


def _record_timing(km, durations):
    """Dauer je Schritt eines fertigen Baus merken (fuer die Restzeit-Schaetzung)."""
    if len(durations) != len(STEPS):
        return
    with _status_lock:
        data = (_load_timing() + [{"km": km, "steps": [round(d, 1) for d in durations]}])[-TIMING_KEEP:]
        path = _timing_path()
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".timing")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2) if n else 0.0


def eta(step, in_step, km, samples=None):
    """Grobe Restzeit in Sekunden aus frueheren Bauten (Median je Schritt, nach Streckenlaenge skaliert); None ohne Daten.
    Der laufende Schritt zaehlt mit dem, was von ihm voraussichtlich noch uebrig ist (mindestens nichts)."""
    samples = _load_timing() if samples is None else samples
    if not samples or step is None or not 0 <= step < len(STEPS):
        return None

    def est(i):
        if km:
            rates = [x["steps"][i] / x["km"] for x in samples if x.get("km")]
            if rates:
                return _median(rates) * km
        return _median([x["steps"][i] for x in samples])
    rest = max(0.0, est(step) - max(0.0, in_step)) + sum(est(i) for i in range(step + 1, len(STEPS)))
    return int(round(rest))


def _ahead(user, trip_id):
    """Wie viele Bauten vor dieser Reise dran sind (der laufende zaehlt mit)."""
    with _q.mutex:
        waiting = list(_q.queue)
    key = (user, trip_id)
    n = waiting.index(key) if key in waiting else len(waiting)
    return n + (1 if _current and _current != key else 0)


def _points(gpx):
    return [(float(a), float(b)) for a, b in re.findall(r'<trkpt lat="([-\d.]+)" lon="([-\d.]+)"', gpx)]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(user, trip_id, steps):
    """Baut synchron ein Paket und gibt das Manifest zurueck (Fehler: Status 'error' + Ausnahme)."""
    trip = trips.get(user, trip_id)
    if not trip:
        raise KeyError(trip_id)
    t_start = time.monotonic()
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
    tmp = os.path.join(_base(user, trip_id), stamp + ".tmp")
    started = time.time()
    marks, km = [], None   # Startzeit je Schritt (Restzeit aus frueheren Bauten), Laenge nach der Planung

    def prog(i):
        doing, _, pct = STEPS[i]
        marks.append(time.time())
        return _set_status(user, trip_id, state="building", msg=doing, progress=pct, step=i, started=started,
                           step_at=marks[-1], km=km)

    try:
        free = shutil.disk_usage(os.environ.get("DATA_DIR", "/data")).free
        if free < MIN_FREE_BYTES:
            raise RuntimeError(f"Kein Platz auf dem Server: {free / 1e9:.1f} GB frei, ein Paket braucht mit Reserve "
                               f"{MIN_FREE_BYTES / 1e9:.0f} GB – alte Reisen löschen oder später nochmal.")
        os.makedirs(tmp)
        prog(0)
        plan = steps.plan(trip)
        if plan.get("too_long"):
            import planner
            raise planner.PlanError(f"Eine Etappe ist länger als {plan['max_km']:.0f} km – auf der Webseite "
                                    "„Nacht“ an Zwischenpunkten setzen und neu schicken.")
        pts = _points(plan["gpx"])
        km = plan.get("km")
        prog(1)
        steps.roads(os.path.join(tmp, "strassen.tar"), pts)
        rings = corridor.outline_rings(pts, OUTLINE_KM)
        prog(2)
        map_key = steps.map(os.path.join(tmp, "karte.pmtiles"), corridor.outline_rings(pts, MAP_KM))
        steps.style(tmp)
        prog(3)
        analysis = steps.analyze(plan["gpx"].encode())
        with open(os.path.join(tmp, "reise.json"), "w", encoding="utf-8") as f:
            json.dump(reisedaten.build(trip, plan, analysis), f, ensure_ascii=False)
        prog(4)
        files = []
        for root, _, names in os.walk(tmp):
            for n in sorted(names):
                p = os.path.join(root, n)
                files.append({"name": os.path.relpath(p, tmp), "size": os.path.getsize(p), "sha256": _sha256(p)})
        m = {"format": FORMAT, "trip_id": trip_id, "name": trip["name"],
             "built": datetime.datetime.now().isoformat(timespec="seconds"),
             "data_dates": {"strassen": steps.roads_date(), "karte": map_key},
             "vehicle": trip["vehicle"], "mode": trip.get("mode", "fast"),
             "leg_modes": plan.get("leg_modes") or [trip.get("mode", "fast")] * (len(trip["stops"]) - 1),
             # Maut/Faehren meiden wie geplant – RigNavi uebernimmt es beim Laden der Reise
             "avoid_toll": bool((trip.get("avoid") or {}).get("toll")),
             "avoid_ferry": bool((trip.get("avoid") or {}).get("ferry")),
             "polygon": rings[0], "holes": rings[1:], "files": sorted(files, key=lambda f: f["name"])}
        with open(os.path.join(tmp, "paket.json"), "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)
        final = tmp[:-4]
        os.replace(tmp, final)
        base = _base(user, trip_id)
        for n in os.listdir(base):
            p = os.path.join(base, n)
            if os.path.isdir(p) and p != final:
                shutil.rmtree(p, ignore_errors=True)
        end = time.time()
        _set_status(user, trip_id, state="ready", msg="", progress=100, took=int(end - started))
        _record_timing(km, [b - a for a, b in zip(marks, marks[1:] + [end])])
        usage.add(user, "rig", build_min=(time.monotonic() - t_start) / 60, build_bytes=sum(f["size"] for f in files))
        _prune_user(user, keep=trip_id)
        log.info("Paket %s/%s fertig: %.0f MB", user, trip_id, sum(f["size"] for f in files) / 1e6)
        return m
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        log.warning("Paket %s/%s fehlgeschlagen: %s", user, trip_id, e)
        _set_status(user, trip_id, state="error", msg=str(e)[:300], progress=0,
                    step=_last_step(user, trip_id))
        raise


def _last_step(user, trip_id):
    """Bei welchem Schritt der Bau stehen blieb (fuer die Fehleranzeige)."""
    try:
        with open(_status_path(user, trip_id), encoding="utf-8") as f:
            return json.load(f).get("step")
    except (OSError, ValueError):
        return None


def _prune_user(user, keep):
    """Hoechstens MAX_PACKAGES_PER_USER Pakete je Benutzer (die neuesten); die Reisen selbst bleiben."""
    root = os.path.join(os.environ.get("DATA_DIR", "/data"), "packages", user.strip().lower())
    built = []
    for tid in os.listdir(root) if os.path.isdir(root) else []:
        d = current_dir(user, tid)
        if d:
            built.append((os.path.basename(d), tid))
    for _, tid in sorted(built, reverse=True)[MAX_PACKAGES_PER_USER:]:
        if tid != keep:
            log.info("Paket %s/%s entfernt (mehr als %d Pakete)", user, tid, MAX_PACKAGES_PER_USER)
            delete(user, tid)


def recover():
    """Nach einem Neustart: halbe Bauten weg, haengende Status auf Fehler (die Warteschlange ist weg)."""
    root = os.path.join(os.environ.get("DATA_DIR", "/data"), "packages")
    for user in os.listdir(root) if os.path.isdir(root) else []:
        for tid in os.listdir(os.path.join(root, user)):
            base = os.path.join(root, user, tid)
            for n in os.listdir(base):
                if n.endswith(".tmp") and os.path.isdir(os.path.join(base, n)):
                    shutil.rmtree(os.path.join(base, n), ignore_errors=True)
            if status(user, tid)["state"] in ("queued", "building"):
                _set_status(user, tid, state="error", msg="Bau unterbrochen (Server-Neustart) – bitte neu schicken.",
                            progress=0)


def delete(user, trip_id):
    if re.match(trips.ID_RE, trip_id or ""):
        shutil.rmtree(_base(user, trip_id), ignore_errors=True)


# ---------------------------------------------------------------- Warteschlange
_q = queue.Queue()
_pending = set()
_lock = threading.Lock()
_worker = None
_current = None   # (benutzer, reise) des gerade laufenden Baus


def real_steps():
    import mapextract
    import planner

    return Steps(
        plan=lambda trip: planner.plan(trip["stops"], "rig", trip.get("mode", "fast"), trip["vehicle"], trip["name"],
                                       avoid=trip.get("avoid")),
        roads=lambda dst, pts: corridor.write_extract(VALHALLA_TAR, dst, ROADS_KM, pts),
        map=_map_step,
        style=mapextract.copy_style,
        analyze=_analyze_step,
        roads_date=lambda: datetime.date.fromtimestamp(os.path.getmtime(VALHALLA_TAR)).isoformat(),
    )


def _analyze_step(gpx):
    """Wie die Webseite: GPX mit mehreren Etappen -> Etappe fuer Etappe pruefen (analyze.run_trip),
    sonst wuerde eine ganze Reise an der 1000-km-Grenze der Einzelanalyse scheitern."""
    import analyze

    opts = {"rig": True}
    stages, gname = analyze.parse_stages(gpx)
    if len(stages) >= 2:
        return analyze.run_trip(stages, gname, "reise.gpx", lambda p, m: None, {}, opts)
    return analyze.analyze(gpx, "reise.gpx", options=opts)


def _map_step(out, rings):
    import mapextract

    region = out + ".region.geojson"
    with open(region, "w") as f:
        json.dump(corridor.geojson(rings[0], rings[1:]), f)
    try:
        url = mapextract.latest_build()
        mapextract.extract(region, out, url)
        return url.rsplit("/", 1)[-1]
    finally:
        os.remove(region)


def _run():
    global _current
    # Paketbau (Strassendaten/Karten ausschneiden) ganz hinten anstellen – dauert dann etwas laenger, der Server bleibt flott
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 15)
    except (OSError, AttributeError):
        pass
    while True:
        user, trip_id = _q.get()
        with _lock:
            _pending.discard((user, trip_id))
        _current = (user, trip_id)
        try:
            build(user, trip_id, real_steps())
        except Exception:  # noqa: BLE001 – Worker darf nie sterben, Fehler steht im Status
            pass
        finally:
            _current = None
            _q.task_done()


MAX_PENDING_PER_USER = 2   # wartende Paketbauten je Login (jeder belegt den einen Bau-Platz fuer Minuten bis Stunden)


class TooMany(Exception):
    pass


def request(user, trip_id):
    global _worker
    with _lock:
        if (user, trip_id) in _pending:
            return
        if sum(1 for u, _t in _pending if u == user) >= MAX_PENDING_PER_USER:
            raise TooMany(f"Schon {MAX_PENDING_PER_USER} Navi-Pakete in Arbeit – bitte warten, bis eins fertig ist.")
        _pending.add((user, trip_id))
        if _worker is None:
            _worker = threading.Thread(target=_run, name="paketbau", daemon=True)
            _worker.start()
    _set_status(user, trip_id, state="queued", msg="wartet auf den Bau", progress=0, queued_at=time.time())
    _q.put((user, trip_id))
