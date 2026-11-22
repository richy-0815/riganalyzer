"""Nachtplatz-Vorschlaege fuer den Routenplaner (RigAnalyzer, seit v0.21.0).

Ueberschreitet eine Etappe das Tageslimit (Fahrzeit ODER Strecke, was zuerst erreicht ist), sucht der Planer
im Fenster um diese Stelle (80–110 % des Limits) Wohnmobil-Stellplaetze und Campingplaetze aus den lokalen
OSM-Daten, die zum Fahrzeug passen (Laenge/Gewicht/Hoehe laut OSM; unbekannt zaehlt als „bitte pruefen“).
Danach geht es ab der Limit-Stelle mit dem naechsten Tag weiter, bis der Rest der Etappe ins Limit passt.

Fahrzeit entlang der Strecke: Valhalla liefert sie je Leg; innerhalb eines Legs wird sie nach Strecke verteilt.
"""

import logging
import math

import numpy as np

log = logging.getLogger("nights")

WIN_FROM, WIN_TO = 0.8, 1.1     # Suchfenster als Anteil des Tageslimits
REST_OK = 1.15                  # Rest der Etappe bis 115 % des Limits: kein weiterer Vorschlag noetig
RADIUS_M = 5000                 # so weit abseits der Strecke darf ein Platz liegen
PER_NIGHT = 4                   # Vorschlaege je Nacht
MAX_NIGHTS = 12


class _Proj:
    """Lokale Projektion in Metern (wie analyze.Proj, ohne den schweren Import)."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.cos(math.radians(lat0)) * 111320.0
        self.ky = 110574.0

    def fwd(self, lat, lon):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * self.ky


def _profile(legs):
    """legs [{shape:[(lat,lon)], km, sec}] -> lat, lon, kumulierte km, kumulierte Minuten (je Shape-Punkt)."""
    lat, lon, km, mins = [], [], [], []
    k0 = m0 = 0.0
    for leg in legs:
        sh = np.asarray(leg["shape"], float)
        if len(sh) < 2:
            continue
        p = _Proj(float(sh[:, 0].mean()), float(sh[:, 1].mean()))
        x, y = p.fwd(sh[:, 0], sh[:, 1])
        d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
        geo = d[-1] or 1.0
        # auf Valhalla-Werte skalieren: Strecke laut Router, Zeit nach Streckenanteil
        k = k0 + d / geo * float(leg["km"])
        m = m0 + d / geo * float(leg["sec"]) / 60.0
        start = 1 if lat else 0           # Legs teilen ihren Grenzpunkt
        lat += list(sh[start:, 0])
        lon += list(sh[start:, 1])
        km += list(k[start:])
        mins += list(m[start:])
        # Aufenthalt am Abschnittsende: die Uhr laeuft weiter, die Strecke nicht
        k0, m0 = k[-1], m[-1] + float(leg.get("stay") or 0)
    return np.asarray(lat), np.asarray(lon), np.asarray(km), np.asarray(mins)


def windows(legs, limit_min, limit_km):
    """Tagesgrenzen einer Etappe -> [{day, i_from, i_to, i_cut, at_min, at_km}] (Indizes in das Profil) + Profil."""
    lat, lon, km, mins = _profile(legs)
    out = []
    if len(km) < 2:
        return out, (lat, lon, km, mins)
    i0 = 0
    for day in range(1, MAX_NIGHTS + 1):
        prog = np.maximum((mins - mins[i0]) / limit_min, (km - km[i0]) / limit_km)
        if prog[-1] <= REST_OK:
            break
        sel = np.nonzero((prog >= WIN_FROM) & (prog <= WIN_TO))[0]
        cut = int(np.searchsorted(prog, 1.0))
        cut = min(max(cut, i0 + 1), len(km) - 1)
        if not len(sel):                  # sehr langes Shape-Stueck ohne Zwischenpunkte
            sel = np.array([cut])
        out.append({"day": day, "i_from": int(sel[0]), "i_to": int(sel[-1]), "i_cut": cut,
                    "at_min": round(float(mins[cut])), "at_km": round(float(km[cut]), 1)})
        i0 = cut
    return out, (lat, lon, km, mins)


def _fits(flags, tg, veh, laenge=None):
    """'ok' | 'likely' | 'unknown' | 'no' fuer das Fahrzeug (Laenge, Gewicht und – falls getaggt – Hoehe am Platz).
    laenge (seit 0.53.0): Urteil der Stellplatz-Laenge {"m", "kind", "level"} aus Website/Luftbild, wenn OSM schweigt."""
    from poi import measure
    length, weight, height = (_f(veh.get(k)) for k in ("length", "weight", "height"))
    ml, mw, mh = flags.get("maxlength"), flags.get("maxweight"), measure(tg.get("maxheight"))
    if (ml and length and length > ml) or (mw and weight and weight > mw) or (mh and height and height > mh):
        return "no"
    if ml and mw:
        return "ok"
    if not ml and laenge and laenge.get("m") and length:
        if length > laenge["m"]:
            return "no" if laenge.get("level") == "confirmed" and laenge.get("kind") == "max" else "likely_no"
        return "ok" if laenge.get("level") == "confirmed" and mw else "likely"
    return "unknown"


def _laenge(key, lat, lon, tags):
    """Gespeichertes Laengen-Urteil eines Platzes (RigAnalyzer haengt sich per hooks ein, sonst None).
    Fehlt es, reiht die Erweiterung den Platz zum Auswerten ein – beim naechsten Planen ist es dann da."""
    import hooks
    fn = hooks.one("nights.camp_length")
    if fn is None:
        return None
    try:
        return fn(key, lat, lon, tags)
    except Exception as e:   # noqa: BLE001 – Beiwerk: Vorschlaege kommen auch ohne
        log.warning("Laengen-Urteil %s: %s", key, e)
        return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _usable(tg):
    """Plaetze, die fuer Wohnmobile nicht in Frage kommen: privat, nur Zelte, ausdruecklich keine Wohnmobile."""
    if tg.get("access") in ("private", "no"):
        return False
    if tg.get("tourism") == "camp_site" and (tg.get("motorhome") == "no" or tg.get("caravans") == "no"
                                             or tg.get("tents") == "only" or tg.get("camp_site") == "basic"
                                             and tg.get("motorhome") != "yes"):
        return False
    return tg.get("tourism") in ("caravan_site", "camp_site")


def suggest(stages, vehicle, limit_min, limit_km, db):
    """stages: [[leg, …], …] mit leg = {shape, km, sec}. -> [{stage, day, at_min, at_km, lat, lon, data, options}]."""
    import poi
    vehicle = vehicle or {}
    out = []
    for s_no, legs in enumerate(stages, 1):
        wins, (lat, lon, km, mins) = windows(legs, limit_min, limit_km)
        for w in wins:
            a, b = w["i_from"], w["i_to"] + 1
            wl, wo = lat[a:b], lon[a:b]
            step = max(1, len(wl) // 400)
            ql, qo = wl[::step], wo[::step]
            item = {"stage": s_no, "day": w["day"], "at_min": w["at_min"], "at_km": w["at_km"],
                    "lat": round(float(lat[w["i_cut"]]), 5), "lon": round(float(lon[w["i_cut"]]), 5),
                    "data": bool(db is not None and db.covers(ql, qo)), "options": []}
            out.append(item)
            if db is None:
                continue
            proj = _Proj(float(np.mean(ql)), float(np.mean(qo)))
            try:
                found = db.camps_near(ql, qo, proj, RADIUS_M)
            except Exception as e:      # noqa: BLE001 – Vorschlaege sind Beiwerk – Planung nie daran scheitern lassen
                log.warning("Platzsuche fehlgeschlagen: %s", e)
                continue
            rx, ry = proj.fwd(wl, wo)
            cands = []
            for el, dist in found:
                tg = el["tags"]
                if not _usable(tg):
                    continue
                la, lo = (el["lat"], el["lon"]) if "lat" in el else (el["center"]["lat"], el["center"]["lon"])
                flags = poi.poi_flags(tg, "camp")
                osm = f'{el["type"]}/{el["id"]}'
                laenge = _laenge(osm, la, lo, tg)
                fits = _fits(flags, tg, vehicle, laenge)
                if fits in ("no", "likely_no"):
                    continue
                if laenge:
                    flags["laenge"] = laenge
                px, py = proj.fwd(la, lo)
                j = a + int(np.argmin(np.hypot(rx - px, ry - py)))
                # Bewertung: wenig Umweg, nah an der Limit-Stelle, Name, bekannte Masse, Ver-/Entsorgung
                score = dist / 1000 * 3 + abs(float(mins[j]) - w["at_min"]) / 10
                score -= (2 if tg.get("name") else 0) + {"ok": 2, "likely": 1}.get(fits, 0)
                score -= sum(1 for k in ("dump", "water", "power") if flags.get(k))
                cands.append((score, {
                    "name": tg.get("name") or ("Wohnmobil-Stellplatz" if tg.get("tourism") == "caravan_site" else "Campingplatz"),
                    "named": bool(tg.get("name")), "kind": tg.get("tourism"), "lat": round(la, 6), "lon": round(lo, 6),
                    "off_km": round(dist / 1000, 1), "at_min": round(float(mins[j])), "at_km": round(float(km[j]), 1),
                    # alle Platz-Merkmale (Details-Ansicht im Planer wie das Karten-Popup)
                    "fits": fits, "flags": {k: v for k, v in flags.items() if v not in (None, "")},
                    "osm": osm}))
            cands.sort(key=lambda c: c[0])
            item["options"] = [c for _, c in cands[:PER_NIGHT]]
    return out

