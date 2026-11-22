"""Warteschlange der Analysen (seit 0.46.0): Laenge vor dem Start, Rechenzeit-Schaetzung, Spur-Wahl, Wartetext.

Ohne FastAPI – main.py verdrahtet es. Spuren: "lokal" (lange Routen), "kurz" (bis SHORT_KM), "live" (Overpass)."""

import collections
import math
import os
import statistics
import threading

import analyze


def lower_priority(nice):
    """Diesen Thread im Linux-Scheduler hinten anstellen (0.26.2): Analysen, Paketbau und Datenabrufe sollen den Server
    nicht ausbremsen – Webseite, Planer und andere Dienste gehen vor. Hoeher setzen darf jeder Prozess ohne Rechte."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), nice)
    except (OSError, AttributeError):
        pass


def route_km(raw: bytes, pts=None) -> float:
    """Summe der Punkt-Abstaende (bei Reisen ueber alle Etappen); kaputte Datei -> 0.0 (Fehler meldet die Analyse).
    pts: schon gelesene Punkte (GPX nur einmal lesen)."""
    if pts is None:
        try:
            pts, _ = analyze.parse_gpx(raw)
        except Exception:  # noqa: BLE001 – nur Vorpruefung
            return 0.0
    km = 0.0
    for (a, b, *_), (c, d, *_) in zip(pts, pts[1:]):
        p1, p2 = math.radians(a), math.radians(c)
        h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
        km += 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))
    return km


class Rate:
    """Sekunden je km aus den letzten echten Analysen (Median), Startwert bis dahin."""

    def __init__(self, start=0.15, keep=20):
        self.start = start
        self._vals = collections.deque(maxlen=keep)
        self._lock = threading.Lock()

    def add(self, km, sec):
        if km >= 1 and sec >= 0:
            with self._lock:
                self._vals.append(sec / km)

    def value(self):
        with self._lock:
            return statistics.median(self._vals) if self._vals else self.start

    def est(self, km):
        return max(3, km * self.value())


def lane_for(km, live, short_km, short_jobs):
    if live:
        return "live"
    return "kurz" if short_jobs > 0 and km <= short_km else "lokal"


def wait_text(jobs, job_id, slots):
    """„Platz N in der Warteschlange · noch ca. M Min.“ fuer einen wartenden Job, sonst None."""
    j = jobs.get(job_id)
    if not j or j.get("state") != "queued":
        return None
    lane = j.get("lane", "lokal")
    same = [x for x in jobs.values() if x.get("lane", "lokal") == lane and not x.get("cancel")]
    running = sum(max(0.0, x.get("est", 0) * (1 - min(100, x.get("progress", 0)) / 100)) for x in same if x["state"] == "running")
    ahead = [x for x in same if x["state"] == "queued" and x.get("seq", 0) < j.get("seq", 0)]
    sec = (running + sum(x.get("est", 0) for x in ahead)) / max(1, slots.get(lane, 1))
    head = f"Platz {len(ahead) + 1} in der Warteschlange · noch "
    return head + ("unter 1 Min." if sec < 60 else f"ca. {math.ceil(sec / 60)} Min.")
