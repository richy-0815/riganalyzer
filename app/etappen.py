"""Lange Einzelstrecke in Etappen teilen (0.47.0, Ticket #5).

Ein GPX mit EINEM Track ueber der Analyse-Grenze (MAX_KM, 1000 km) wird nicht mehr abgelehnt, sondern in
gleich lange Tagesetappen geschnitten – moeglichst an einem Ort in der Naehe der idealen Schnittstelle (Stadt vor
Kleinstadt vor Dorf). Jede Etappe bleibt unter der Grenze und wird einzeln analysiert (wie eine Reise mit mehreren
Tracks). Orte kommen ueber `places(lat, lon, radius_m) -> [(name, art, dist_m)]` herein (osmdb), sonst wird nach km
geschnitten.
"""
import math

import numpy as np

STAGE_KM = 500.0          # Ziel je Etappe: ~6 h Fahrt mit Gespann/Wohnmobil
WINDOW = 0.15             # Schnitt darf um 15 % einer Etappenlaenge wandern, um einen Ort zu treffen
SAMPLE_KM = 5.0           # im Fenster alle 5 km nach Orten schauen
PLACE_R = 3000.0          # Ort hoechstens 3 km neben der Strecke
KIND_COST = {"city": 0.0, "town": 0.15, "village": 0.45, "suburb": 0.6}


def track_km(pts):
    """Kumulierte km je Punkt (Haversine)."""
    a = np.radians(np.asarray([(p[0], p[1]) for p in pts], float))
    if len(a) < 2:
        return np.zeros(len(a))
    dlat, dlon = np.diff(a[:, 0]), np.diff(a[:, 1])
    h = np.sin(dlat / 2) ** 2 + np.cos(a[:-1, 0]) * np.cos(a[1:, 0]) * np.sin(dlon / 2) ** 2
    return np.concatenate([[0.0], np.cumsum(2 * 6371.0088 * np.arcsin(np.sqrt(np.minimum(h, 1.0))))])


def parts_for(total_km, max_km, target_km=STAGE_KM):
    """Anzahl Etappen: so viele, dass jede ~target_km lang ist, aber sicher unter max_km bleibt."""
    n = max(2, math.ceil(total_km / max(target_km, 50.0)))
    return max(n, math.ceil(total_km / (max_km * 0.95)))


def _best_cut(km, pts, ideal, part, places):
    """Index der Schnittstelle nahe `ideal` (km), wenn moeglich an einem Ort -> (index, ortsname|"")."""
    lo, hi = ideal - WINDOW * part, ideal + WINDOW * part
    nearest = int(np.argmin(np.abs(km - ideal)))
    if places is None:
        return nearest, ""
    best = None
    for s in np.arange(max(lo, km[1]), min(hi, km[-2]), SAMPLE_KM):
        i = int(np.searchsorted(km, s))
        for name, kind, dist in places(pts[i][0], pts[i][1], PLACE_R):
            if kind not in KIND_COST or not name or dist > PLACE_R:
                continue
            cost = KIND_COST[kind] + abs(km[i] - ideal) / (WINDOW * part) + dist / PLACE_R * 0.3
            if best is None or cost < best[0]:
                best = (cost, i, name)
    return (best[1], best[2]) if best else (nearest, "")


def split_long(pts, max_km, target_km=STAGE_KM, places=None):
    """pts [(lat, lon, ele)] -> (stages [(name, None, pts)], info {km, parts, stage_km}). Nachbaretappen teilen den
    Schnittpunkt. Namen „Etappe n: A → B“ aus den Orten an den Schnitten (ohne Ort: nur „Etappe n“)."""
    km = track_km(pts)
    total = float(km[-1])
    n = parts_for(total, max_km, target_km)
    part = total / n
    cuts, names = [0], [""]
    for k in range(1, n):
        i, name = _best_cut(km, pts, part * k, part, places)
        i = min(max(i, cuts[-1] + 1), len(pts) - 2)
        cuts.append(i)
        names.append(name)
    cuts.append(len(pts) - 1)
    names.append("")
    stages = []
    for k in range(n):
        a, b = cuts[k], cuts[k + 1]
        title = f"{names[k] or 'Start' if k == 0 else names[k] or '…'} → {names[k + 1] or ('Ziel' if k == n - 1 else '…')}"
        named = any(names[k:k + 2])
        stages.append((f"Etappe {k + 1}: {title}" if named else f"Etappe {k + 1}", None, list(pts[a:b + 1])))
    lens = [float(km[cuts[k + 1]] - km[cuts[k]]) for k in range(n)]
    return stages, {"km": round(total, 1), "parts": n, "stage_km": round(max(lens), 1)}
