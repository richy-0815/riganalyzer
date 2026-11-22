"""Verkehrslage fuer RigNavi: Staus und Sperrungen auf deutschen Autobahnen.

Quelle: Autobahn-API des Bundes (verkehr.autobahn.de), offen, ohne Konto. Der Server holt alle
REFRESH Sekunden alle Meldungen (im Hintergrund, 8 parallel) und filtert je Anfrage die heraus,
die auf der Route liegen – in derselben Fahrtrichtung (sonst kaeme die Gegenfahrbahn mit).
"""

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import numpy as np

log = logging.getLogger("traffic")

BASE = "https://verkehr.autobahn.de/o/autobahn"
REFRESH = 300
NEAR_M = 80
UA = {"User-Agent": "RideAnalyzer-Prototyp/0.8 (+https://riganalyzer.petrick.top)"}
KIND = {"warning": "stau", "closure": "sperrung"}

_state = {"t": 0.0, "items": [], "busy": False, "error": None}
_lock = threading.Lock()


def _clean(title):
    """Interne Kuerzel raus: 'A9 RF M AKR Fahrbahn…' / '9 HDF_2026_049819_RF M Kreuz …'."""
    import re
    t = re.sub(r"\b\S*_\S*\b", "", title or "")
    t = re.sub(r"\bRF [A-Z]{1,3}\b", "", t)
    return re.sub(r"\s{2,}", " ", t).strip(" |-")


def _parse(kind, x):
    # Sperrungen: nur aktuell gueltige und nur ganze Fahrbahnen (keine Auf-/Abfahrten)
    if kind == "closure" and (x.get("future") or x.get("display_type") == "CLOSURE_ENTRY_EXIT"):
        return None
    pts = []
    g = x.get("geometry") or {}
    if g.get("type") == "LineString":
        pts = [(c[1], c[0]) for c in g.get("coordinates", [])]
    elif x.get("coordinate"):
        c = x["coordinate"]
        pts = [(float(c["lat"]), float(c["long"]))]
    if not pts:
        return None
    desc = [d.strip() for d in (x.get("description") or []) if d and d.strip()]
    # die Zeilen, die Autofahrer interessieren
    if kind == "warning":
        keep = [d for d in desc if d.startswith("-") or "Reisezeitverlust" in d]
    else:   # Sperrung: Zeitraum und Laenge, ohne Verwaltungstext
        keep = [d for d in desc if not d.startswith("Die Baustelle") and "nicht ermittelt" not in d][:3]
    try:
        delay = int(x.get("delayTimeValue") or 0)
    except ValueError:
        delay = 0
    return {"kind": KIND[kind], "title": _clean(x.get("title")), "dir": (x.get("subtitle") or "").strip(),
            "delay": delay, "text": [d.lstrip("- ").strip() for d in keep][:4], "pts": pts[::max(1, len(pts) // 60)],
            "blocked": str(x.get("isBlocked")).lower() == "true"}


def _fetch_all():
    with httpx.Client(timeout=20, headers=UA) as c:
        roads = c.get(f"{BASE}/").json().get("roads", [])

        def one(job):
            road, kind = job
            try:
                with httpx.Client(timeout=20, headers=UA) as cc:
                    data = cc.get(f"{BASE}/{road.strip()}/services/{kind}").json()
                return [p for p in (_parse(kind, x) for x in data.get(kind, [])) if p]
            except Exception:  # noqa: BLE001 – eine Strasse/Art ohne Daten darf den Rest nicht stoppen
                return []

        jobs = [(r, k) for r in roads for k in KIND]
        with ThreadPoolExecutor(max_workers=8) as ex:
            out = [it for chunk in ex.map(one, jobs) for it in chunk]
    # Sperrungen mit Zukunftsdatum oder reine Hinweise ohne Wirkung bleiben trotzdem drin –
    # die Autobahn-API meldet unter "closure" nur aktuell gueltige Sperrungen
    return out


def _refresh():
    try:
        items = _fetch_all()
        with _lock:
            _state.update(t=time.time(), items=items, error=None)
        log.info("Verkehrslage: %d Meldungen (Staus/Sperrungen)", len(items))
    except Exception as e:  # noqa: BLE001 – Hintergrundabruf darf nie abbrechen, Fehler steht im Status
        log.warning("Verkehrslage nicht abrufbar: %s", e)
        with _lock:
            _state["error"] = str(e)
    finally:
        _state["busy"] = False


def items():
    """Aktuelle Meldungen; stoesst bei Bedarf eine Aktualisierung im Hintergrund an."""
    with _lock:
        stale = time.time() - _state["t"] > REFRESH
        if stale and not _state["busy"]:
            _state["busy"] = True
            threading.Thread(target=_refresh, daemon=True, name="verkehr").start()
        return list(_state["items"]), _state["t"]


def on_route(route, wait=20.0):
    """route: [[lat, lon], ...] -> Meldungen auf der Route (gleiche Richtung), nach km sortiert."""
    if len(route) < 2:
        return {"items": [], "stand": None}
    its, t = items()
    if not its:   # erster Aufruf nach dem Start: kurz auf die Daten warten
        end = time.time() + wait
        while not its and time.time() < end:
            time.sleep(0.5)
            its, t = items()
    lat0 = route[0][0]
    k = math.cos(math.radians(lat0)) * 111320.0
    ra = np.asarray(route, float)
    R = np.column_stack([ra[:, 1] * k, ra[:, 0] * 110574.0])
    cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(R, axis=0).T))])
    lo_b, hi_b = R.min(0) - 500, R.max(0) + 500
    out = []
    for it in its:
        pa = np.asarray(it["pts"], float)
        P = np.column_stack([pa[:, 1] * k, pa[:, 0] * 110574.0])
        if not np.any(np.all((P >= lo_b) & (P <= hi_b), axis=1)):
            continue
        D = np.hypot(P[:, None, 0] - R[None, :, 0], P[:, None, 1] - R[None, :, 1])   # Meldungspunkte x Route
        pi, i = np.unravel_index(int(np.argmin(D)), D.shape)
        if D[pi, i] > NEAR_M:
            continue
        if len(P) >= 2:   # Richtung der Meldung vs. Fahrtrichtung der Route an dieser Stelle
            j, kk = min(i + 1, len(R) - 1), max(i - 1, 0)
            if float(np.dot(R[j] - R[kk], P[-1] - P[0])) < 0:
                continue
        out.append({"km": round(float(cum[i]) / 1000, 1), "kind": it["kind"], "title": it["title"], "dir": it["dir"],
                    "delay": it["delay"], "text": it["text"], "blocked": it["blocked"],
                    "lat": it["pts"][0][0], "lon": it["pts"][0][1]})
    seen, uniq = set(), []
    for o in sorted(out, key=lambda o: o["km"]):
        key = (o["kind"], o["title"], o["dir"])
        if key not in seen:
            seen.add(key)
            uniq.append(o)
    out = uniq
    return {"items": out, "stand": int(t) if t else None}


# beim Start schon einmal laden, damit die erste Anfrage nicht warten muss
items()
