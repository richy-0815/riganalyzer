"""Routenplaner: fragt den eigenen Valhalla-Dienst (Open Source, Docker auf dem Server) und liefert GPX.

Ride: Motorrad-Profil in drei Spielarten (schnell / ohne Autobahn / kurvig).
Rig:  plant mit den Fahrzeugmassen – bis 3,5 t ohne Anhaenger als Pkw mit Hoehe/Breite
      (sonst wuerden Lkw-Verbote unnoetig umfahren), schwerer oder mit Anhaenger als Lkw
      mit Hoehe, Breite, Laenge, Gewicht und Achslast.
Haltepunkte mit "stop" (Uebernachtung) teilen die Route in Etappen = Tracks in der GPX,
die der Analyzer dann als Reise mit Fahrplan behandelt.
"""

import json
import logging
import os
from xml.sax.saxutils import escape

import httpx

log = logging.getLogger("planner")

VALHALLA_URL = os.environ.get("VALHALLA_URL", "http://valhalla:8002")
AREA = os.environ.get("PLAN_AREA", "Deutschland, Österreich, Schweiz, Dänemark, Norwegen, Schweden und der Korridor Richtung Verdon/Provence/Katalonien")
MAX_POINTS = 25
MAX_KM = float(os.environ.get("MAX_KM", "1000"))   # Grenze der Analyse je Strecke bzw. Etappe

RIDE_MODES = {
    "fast": ("motorcycle", {"use_highways": 1.0, "use_tolls": 0.5}),
    "noway": ("motorcycle", {"use_highways": 0.0, "use_tolls": 0.0}),
    # kleine Strassen: Roller-Profil meidet Autobahnen/Hauptstrassen, Tempo hoch gesetzt
    "curvy": ("motor_scooter", {"use_primary": 0.0, "use_highways": 0.0, "top_speed": 120, "use_hills": 0.5}),
}


class PlanError(Exception):
    """Fehler mit Text fuer die Seite."""


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


def avoid_flags(avoid):
    """{"toll": bool, "ferry": bool} aus Anfrage/Tour (Ticket #22, 0.42.0) – alles andere zaehlt als nein."""
    a = avoid if isinstance(avoid, dict) else {}
    return {"toll": a.get("toll") is True, "ferry": a.get("ferry") is True}


def _rig_costing(veh, avoid_motorway, avoid=None):
    """Wie RigCosting.kt in RigNavi (gleiche Werte!). avoid: Maut/Faehren meiden (0.42.0) – nur gesetzt, wenn gewollt,
    sonst gilt Valhallas Standard (0,5)."""
    h, w = _num(veh.get("height"), 1.5, 5, 3.2), _num(veh.get("width"), 1, 4, 2.35)
    length, t = _num(veh.get("length"), 3, 25, 7.5), _num(veh.get("weight"), 1, 40, 3.5)
    vmax = int(_num(veh.get("vmax"), 40, 130, 100))
    common = {"height": h, "width": w, "top_speed": vmax, "exclude_unpaved": True,
              "use_highways": 0.0 if avoid_motorway else 1.0}
    av = avoid_flags(avoid)
    if av["toll"]:
        common["use_tolls"] = 0.0
    if av["ferry"]:
        common["use_ferry"] = 0.0
    if t <= 3.5 and not veh.get("trailer"):
        return "auto", common
    return "truck", {**common, "length": length, "weight": t,
                     "axle_load": _num(veh.get("axle"), 0.5, 12, 2.5), "hazmat": False}


def _decode(poly, precision=6):
    """Valhalla-Polyline (Genauigkeit 1e6) -> [(lat, lon)]."""
    out, idx, lat, lon, f = [], 0, 0, 0, 10 ** precision
    while idx < len(poly):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(poly[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += d
            else:
                lon += d
        out.append((lat / f, lon / f))
    return out


def leg_groups(points, default):
    """Abschnitte gleicher Einstellung in Folge -> [(modus, [punkt-indizes])]. points[i]["mode"] gilt fuer den
    Abschnitt BIS zu Punkt i (Start hat keinen); fehlt er, gilt default. Gruppen teilen den Grenzpunkt."""
    modes = [(p.get("mode") or default) for p in points[1:]]
    groups = []
    for i, m in enumerate(modes, start=1):
        if groups and groups[-1][0] == m:
            groups[-1][1].append(i)
        else:
            groups.append((m, [i - 1, i]))
    return groups


def _avoid(avoid_zones, start, end):
    """Umweltzonen-IDs -> (exclude_polygons, Hinweis). Zonen, die Start oder Ziel (lat, lon) enthalten, bleiben
    drin und kommen in den Hinweis (seit v0.19.0)."""
    import lez
    from shapely.geometry import Point
    if not isinstance(avoid_zones, list):
        return [], None
    ids = [z for z in avoid_zones if isinstance(z, str)]
    polys, kept = lez.exclude_polygons(ids, [start, end])
    by_id = {z["id"]: z for z in lez.zones() or []}
    notes = []
    for zid in kept:
        z = by_id[zid]
        wo = "Start" if z["geom"].intersects(Point(start[1], start[0])) else "Ziel"
        notes.append(f"{wo} liegt in der Umweltzone {z['name']}")
    return polys, "; ".join(dict.fromkeys(notes)) or None


def _diagnose(ask, idx, coords, pts, costing, opts, app):
    """Nach „keine Route“: den ersten Abschnitt finden, der allein nicht geht, und sagen, ob es am Fahrzeug liegt."""
    def nm(i):
        return (pts[i].get("name") or f"Punkt {i + 1}").split(",")[0]

    for a, b in zip(idx, idx[1:]):
        locs = [{"lat": coords[a][0], "lon": coords[a][1], "type": "break"},
                {"lat": coords[b][0], "lon": coords[b][1], "type": "break"}]
        r, _ = ask(locs, costing, opts)
        if r.status_code == 200:
            continue
        if app == "rig":
            car, _ = ask(locs, "auto", {})
            if car.status_code == 200:
                return (f"Keine Route für den Abschnitt {nm(a)} → {nm(b)} mit eurem Fahrzeug: Für Pkw geht es, für euer Fahrzeug "
                        f"ist die Zufahrt laut Karte gesperrt (Durchfahrtsverbot, Gewicht, Länge oder Höhe). "
                        f"„{nm(b)}“ etwas verschieben oder einen anderen Platz wählen.")
        return (f"Keine Route für den Abschnitt {nm(a)} → {nm(b)}. Liegt „{nm(b)}“ an einer Straße im "
                f"Planungsgebiet ({AREA})? Sonst den Punkt etwas verschieben.")
    return None


def plan(points, app, mode="fast", vehicle=None, name=None, avoid_zones=None, nights=None, avoid=None):
    """points: [{lat, lon, stop?, mode?}] -> dict(gpx, km, minutes, stages, leg_modes, costing, night_hints).
    nights (nur Rig, seit v0.21.0): {"hours": 5, "km": 500} -> Nachtplatz-Vorschlaege fuer zu lange Etappen.
    Abschnitte mit eigener Einstellung (mode je Punkt) werden stueckweise geplant und aneinandergehaengt."""
    pts = [p for p in points or [] if isinstance(p, dict)]
    if len(pts) < 2:
        raise PlanError("Mindestens Start und Ziel setzen.")
    if len(pts) > MAX_POINTS:
        raise PlanError(f"Höchstens {MAX_POINTS} Punkte.")
    allowed = ("fast", "noway") if app == "rig" else tuple(RIDE_MODES)
    mode = mode if mode in allowed else "fast"
    for p in pts:
        if p.get("mode") not in allowed:
            p.pop("mode", None)
    for i, p in enumerate(pts):   # Aufenthalt am Zwischenpunkt: Minuten, nur zwischen Start und Ziel
        st = stay_min(p.get("stay"))
        if st and 0 < i < len(pts) - 1 and not p.get("stop"):
            p["stay"] = st
        else:
            p.pop("stay", None)
    coords = []
    for p in pts:
        lat, lon = _num(p.get("lat"), -90, 90, None), _num(p.get("lon"), -180, 180, None)
        if lat is None or lon is None:
            raise PlanError("Ungültiger Punkt.")
        coords.append((lat, lon))

    # Umweltzonen umfahren: nur Rig; Zonen an Start/Ziel bleiben drin (Hinweis)
    excl, lez_note = _avoid(avoid_zones, coords[0], coords[-1]) if app == "rig" else ([], None)

    def ask(locs, costing, opts):
        nonlocal excl, lez_note
        req = {"locations": locs, "costing": costing, "costing_options": {costing: opts},
               "units": "kilometers", "language": "de-DE", "directions_type": "none"}
        if excl:
            req["exclude_polygons"] = excl
        try:
            r = httpx.post(f"{VALHALLA_URL}/route", json=req, timeout=180)
            if excl and r.status_code != 200:   # mit Ausschluss keine Route -> ohne Ausschluss neu planen
                log.info("Valhalla ohne Route mit Umweltzonen-Ausschluss, plane ohne")
                excl, lez_note = [], "; ".join(filter(None, [lez_note, "Umfahren nicht möglich"]))
                del req["exclude_polygons"]
                r = httpx.post(f"{VALHALLA_URL}/route", json=req, timeout=180)
        except httpx.HTTPError as e:
            log.warning("Valhalla nicht erreichbar: %s", e)
            raise PlanError("Der Routenplaner ist gerade nicht erreichbar.") from e
        return r, (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})

    groups = leg_groups(pts, mode)
    legs, ends, note, costing_used, total_len, total_time = [], [], None, None, 0.0, 0.0
    has_toll = has_ferry = False
    for gmode, idx in groups:
        # Zwischenpunkte "through": keine Wende am Punkt; Uebernachtungen, Gruppengrenzen + Ende "break"
        # auch Punkte mit Aufenthalt: dort haelt man an, und der Abschnitt endet dort (km fuer den Fahrplan)
        locs = [{"lat": coords[i][0], "lon": coords[i][1],
                 "type": "break" if i in (idx[0], idx[-1]) or pts[i].get("stop") or pts[i].get("stay") else "through"}
                for i in idx]
        # Fahrtrichtung (Eckdaten aus einem GPX-Import): Punkte auf Autobahnen sonst oft auf der Gegenfahrbahn
        for loc, i in zip(locs, idx):
            h = _num(pts[i].get("heading"), 0, 360, None)
            if h is not None and h == h:   # nicht NaN
                loc["heading"], loc["heading_tolerance"] = round(h) % 360, 60
        if app == "rig":
            costing, opts = _rig_costing(vehicle or {}, gmode == "noway", avoid)
        else:
            costing, opts = RIDE_MODES.get(gmode, RIDE_MODES["fast"])
        r, data = ask(locs, costing, opts)
        # Roller-Profil (kleine Strassen) darf keine Kraftfahrstrassen: ist ein Punkt nur so erreichbar,
        # mit dem Motorrad ohne Autobahn nochmal versuchen
        if r.status_code != 200 and costing == "motor_scooter" and data.get("error_code") in (442, 443):
            costing, opts = RIDE_MODES["noway"]
            r, data = ask(locs, costing, opts)
            note = "Nur über kleine Straßen ging es nicht durchgehend – geplant ohne Autobahn."
        if r.status_code != 200:
            code, msg = data.get("error_code"), data.get("error", getattr(r, "text", "")[:200])
            log.info("Valhalla %s: %s", code, msg)
            where = ""
            if len(groups) > 1:
                def nm(i):
                    return pts[i].get("name") or f"Punkt {i + 1}"

                where = f" für den Abschnitt {nm(idx[0])} → {nm(idx[-1])}"
            if code in (171, 170, 442, 443):
                # Welcher Abschnitt klemmt? Paarweise nachfragen; geht er mit Pkw, liegt es am Fahrzeug
                # (Durchfahrtsverbot, Gewicht/Laenge/Hoehe) – typisch: Platz nur ueber eine gesperrte Zufahrt erreichbar.
                why = _diagnose(ask, idx, coords, pts, costing, opts, app) if len(idx) >= 2 else None
                if why:
                    raise PlanError(why)
                raise PlanError(f"Keine Route{where} gefunden. Liegen alle Punkte an einer Straße im Planungsgebiet ({AREA})?")
            raise PlanError(f"Keine Route{where} gefunden ({msg}).")
        trip = data["trip"]
        has_toll = has_toll or bool(trip["summary"].get("has_toll"))
        has_ferry = has_ferry or bool(trip["summary"].get("has_ferry"))
        costing_used = costing_used or costing
        total_len += trip["summary"]["length"]
        total_time += trip["summary"]["time"]
        brk = [i for i, loc in zip(idx, locs) if loc["type"] == "break"][1:]
        for leg, end in zip(trip["legs"], brk):
            legs.append({"km": round(leg["summary"]["length"], 1), "min": round(leg["summary"]["time"] / 60),
                         "sec": leg["summary"]["time"], "shape": _decode(leg["shape"]),
                         "stay": pts[end].get("stay", 0)})   # Aufenthalt am Ende des Abschnitts (nights.py)
            ends.append(end)
    # Etappen = zusammenhaengende Legs bis zur naechsten Uebernachtung
    stages, cur = [], []
    for leg, end in zip(legs, ends):
        cur.append(leg)
        if pts[end].get("stop") or end == len(pts) - 1:
            stages.append(cur)
            cur = []
    costing = costing_used
    s = {"length": total_len, "time": total_time}
    leg_modes = [(p.get("mode") or mode) for p in pts[1:]]
    title = name or ("Geplante Reise" if len(stages) > 1 else "Geplante Route")
    trk = []
    for n, st in enumerate(stages, 1):
        pts_xml = "".join(f'<trkpt lat="{la:.6f}" lon="{lo:.6f}"/>' for leg in st for la, lo in leg["shape"])
        nm = f"Etappe {n}" if len(stages) > 1 else title
        trk.append(f"<trk><name>{escape(nm)}</name><trkseg>{pts_xml}</trkseg></trk>")
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="RideAnalyzer-Routenplaner (Valhalla)" '
           f'xmlns="http://www.topografix.com/GPX/1/1"><metadata><name>{escape(title)}</name></metadata>{"".join(trk)}</gpx>\n')
    st_out = [{"km": round(sum(leg["km"] for leg in st), 1), "min": sum(leg["min"] for leg in st)} for st in stages]
    return {"gpx": gpx, "km": round(s["length"], 1), "minutes": round(s["time"] / 60), "costing": costing,
            "stages": st_out, "max_km": MAX_KM, "too_long": any(x["km"] > MAX_KM for x in st_out), "note": note,
            "leg_modes": leg_modes, "lez_note": lez_note,
            # je Abschnitt Laenge, Fahrzeit und Zielpunkt – daraus legt der Fahrplan Aufenthalte auf den km
            "legs": [{"km": leg["km"], "min": leg["min"], "to": end} for leg, end in zip(legs, ends)],
            # faehrt die Route trotzdem ueber Maut/Faehre? avoid_note sagt es, wenn „meiden“ an war
            "has_toll": has_toll, "has_ferry": has_ferry, "avoid_note": _avoid_note(avoid, has_toll, has_ferry) if app == "rig" else None,
            "night_hints": night_hints(stages, vehicle, nights) if app == "rig" else None}


def _avoid_note(avoid, has_toll, has_ferry):
    av = avoid_flags(avoid)
    miss = [w for w, on, has in (("Maut", av["toll"], has_toll), ("Fähre", av["ferry"], has_ferry)) if on and has]
    return f"{' und '.join(miss)} ließ sich nicht ganz vermeiden" if miss else None


# ---------------------------------------------------------------- Alternativrouten (nur Rig)
VARIANT_MIN_KM = 1.0      # Alternative muss irgendwo so weit von der Hauptroute abweichen, sonst ist sie keine
VARIANT_SHAPE = 400       # Punkte je Variante fuer die Karte


def _hav(a, b):
    import math
    dla, dlo = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dla / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlo / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(h)))


def _bearing(a, b):
    import math
    la1, la2, dlo = math.radians(a[0]), math.radians(b[0]), math.radians(b[1] - a[1])
    y = math.sin(dlo) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlo)
    return round(math.degrees(math.atan2(y, x)) + 360) % 360


def _thin(shape, n):
    if len(shape) <= n:
        return list(shape)
    step = (len(shape) - 1) / (n - 1)
    return [shape[round(k * step)] for k in range(n)]


def _via_for(alt, main):
    """Punkt der Alternative, der am weitesten von der Hauptroute weg liegt -> (lat, lon, heading, abstand_km).
    Als Zwischenpunkt gesetzt, faehrt der Planer (und RigNavi) genau diese Variante nach."""
    a, m = _thin(alt, 300), _thin(main, 600)
    best, bd = 0, -1.0
    for i, p in enumerate(a):
        d = min(_hav(p, q) for q in m)
        if d > bd:
            best, bd = i, d
    p, q = a[max(0, best - 1)], a[min(len(a) - 1, best + 1)]
    return a[best][0], a[best][1], _bearing(p, q), bd


def _gpx_shape(gpx):
    import re
    return [(float(la), float(lo)) for la, lo in re.findall(r'<trkpt lat="([-\d.]+)" lon="([-\d.]+)"', gpx)]


def variants(points, vehicle=None, mode="fast", avoid_zones=None, avoid=None):
    """Alternativen zur aktuellen Planung -> [{id, label, km, minutes, coords, apply}], erste = aktuelle Planung.
    - Start -> Ziel ohne Zwischenpunkte: Valhalla-Alternativen (alternates=2). apply.via: Zwischenpunkt an der Stelle
      groesster Abweichung (mit Fahrtrichtung) – so bleibt die Variante beim Speichern und in RigNavi erhalten.
    - immer: die Gegenvariante zur Autobahn-Einstellung (apply.mode = "noway" bzw. "fast" fuer alle Abschnitte).
    Fast gleiche Varianten (km und Zeit je unter 1 %) fallen weg."""
    mode = mode if mode in ("fast", "noway") else "fast"
    main = plan(points, "rig", mode, vehicle, None, avoid_zones, avoid=avoid)
    pts = [p for p in points if isinstance(p, dict)]
    main_shape = _gpx_shape(main["gpx"])
    out = [{"id": "aktuell", "label": "Aktuelle Planung", "km": main["km"], "minutes": main["minutes"],
            "coords": _thin(main_shape, VARIANT_SHAPE), "apply": None}]

    def same(km, minutes):
        return any(abs(km - v["km"]) <= v["km"] * 0.01 and abs(minutes - v["minutes"]) <= max(1, v["minutes"] * 0.01) for v in out)

    if len(pts) == 2:
        leg_mode = pts[1].get("mode") if pts[1].get("mode") in ("fast", "noway") else mode
        costing, opts = _rig_costing(vehicle or {}, leg_mode == "noway", avoid)
        c = [(_num(p.get("lat"), -90, 90, 0), _num(p.get("lon"), -180, 180, 0)) for p in pts]
        excl, _note = _avoid(avoid_zones, c[0], c[-1])
        locs = [{"lat": la, "lon": lo, "type": "break"} for la, lo in c]
        for loc, p in zip(locs, pts):
            h = _num(p.get("heading"), 0, 360, None)
            if h is not None and h == h:
                loc["heading"], loc["heading_tolerance"] = round(h) % 360, 60
        req = {"locations": locs, "costing": costing, "costing_options": {costing: opts}, "alternates": 2,
               "units": "kilometers", "language": "de-DE", "directions_type": "none"}
        if excl:
            req["exclude_polygons"] = excl
        try:
            r = httpx.post(f"{VALHALLA_URL}/route", json=req, timeout=180)
            data = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as e:
            log.info("Alternativen nicht verfuegbar: %s", e)
            data = {}
        for n, alt in enumerate(data.get("alternates") or [], 1):
            trip = alt.get("trip") or {}
            shape = [pt for leg in trip.get("legs", []) for pt in _decode(leg["shape"])]
            km, minutes = round(trip["summary"]["length"], 1), round(trip["summary"]["time"] / 60)
            if not shape or same(km, minutes):
                continue
            la, lo, hd, dist = _via_for(shape, main_shape)
            if dist < VARIANT_MIN_KM:
                continue
            out.append({"id": f"alt{n}", "label": f"Alternative {n}", "km": km, "minutes": minutes,
                        "coords": _thin(shape, VARIANT_SHAPE),
                        "apply": {"via": {"lat": round(la, 6), "lon": round(lo, 6), "heading": hd}}})
    # Gegenvariante zur Autobahn: sind alle Abschnitte schon "ohne", dann "mit", sonst "ohne"
    legs = [(p.get("mode") or mode) for p in pts[1:]]
    other = "fast" if all(m == "noway" for m in legs) else "noway"
    try:
        o = plan([{k: v for k, v in p.items() if k != "mode"} for p in pts], "rig", other, vehicle, None, avoid_zones, avoid=avoid)
    except PlanError:
        o = None
    if o and not same(o["km"], o["minutes"]):
        out.append({"id": other, "label": "Ohne Autobahn" if other == "noway" else "Mit Autobahn", "km": o["km"],
                    "minutes": o["minutes"], "coords": _thin(_gpx_shape(o["gpx"]), VARIANT_SHAPE), "apply": {"mode": other}})
    for v in out:
        v["coords"] = [[round(la, 5), round(lo, 5)] for la, lo in v["coords"]]
    return out


def stay_min(v):
    """Aufenthalt in Minuten (0.47.0, Ticket #2): ganze Minuten, hoechstens 24 h; Unsinn (auch True/False) -> 0."""
    if isinstance(v, bool):
        return 0
    m = _num(v, 0, 1440, None)
    return int(round(m)) if m and m == m else 0


def night_hints(stages, vehicle, nights):
    """Tageslimit {"hours", "km"} -> Nachtplatz-Vorschlaege (nights.suggest) oder None (aus / Fehler)."""
    if not isinstance(nights, dict):
        return None
    hours, km = _num(nights.get("hours"), 2, 12, None), _num(nights.get("km"), 100, 1200, None)
    if hours is None and km is None:
        return None
    try:
        import nights as nt
        import osmdb
        return {"hours": hours, "km": km,
                "items": nt.suggest(stages, vehicle, (hours or 99) * 60, km or 9999, osmdb.get())}
    except Exception as e:   # noqa: BLE001 – Vorschlaege sind Beiwerk: die Route kommt trotzdem
        log.warning("Nachtplatz-Vorschlaege fehlgeschlagen: %s", e)
        return None


def status():
    try:
        r = httpx.get(f"{VALHALLA_URL}/status", timeout=5)
        return {"ok": r.status_code == 200, "area": AREA}
    except httpx.HTTPError:
        return {"ok": False, "area": AREA}


# ---------------------------------------------------------------- Ortssuche (Nominatim, OSM)
# Nutzungsregeln: max. 1 Anfrage/s, eigener User-Agent, Ergebnisse cachen.
_geo_cache, _geo_last = {}, [0.0]
GEO_COUNTRIES = os.environ.get("PLAN_COUNTRIES", "de,at,ch,li,fr,es,ad,it,mc")
# Ortssuche: ganz Europa finden, das Planungsgebiet nur bevorzugen (viewbox ohne bounded)
GEO_VIEWBOX = os.environ.get("PLAN_VIEWBOX", "-2.0,55.5,17.5,40.5")   # lon1,lat1,lon2,lat2


GEO_QUEUE_S = 2.5


class GeoBusy(Exception):
    pass


def geocode(q):
    import threading
    import time
    q = " ".join(str(q or "").split())[:120]
    if len(q) < 2:
        return []
    key = q.lower()
    if key in _geo_cache:
        return _geo_cache[key]
    lock = globals().setdefault("_geo_lock", threading.Lock())
    # hoechstens kurz anstellen – sonst warten beliebig viele Suchen am Lock und belegen je einen Thread
    # des gemeinsamen Pools (Login, RigNavi-Anmeldung, Planung standen dann sekundenlang)
    if not lock.acquire(timeout=GEO_QUEUE_S):
        raise GeoBusy("Die Ortssuche ist gerade ausgelastet – bitte gleich nochmal.")
    try:
        wait = 1.05 - (time.time() - _geo_last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = httpx.get("https://nominatim.openstreetmap.org/search",
                          params={"q": q, "format": "jsonv2", "limit": 8, "viewbox": GEO_VIEWBOX,
                                  "addressdetails": 1, "accept-language": "de"},
                          headers={"User-Agent": "RideAnalyzer-Prototyp/0.7 (+https://rideanalyzer.petrick.top)"},
                          timeout=15)
            r.raise_for_status()
            area = set(GEO_COUNTRIES.split(","))
            res = [{"name": h.get("display_name", ""), "lat": float(h["lat"]), "lon": float(h["lon"]),
                    "in_area": (h.get("address") or {}).get("country_code", "") in area} for h in r.json()]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("Ortssuche fehlgeschlagen: %s", e)
            raise PlanError("Die Ortssuche ist gerade nicht erreichbar – Punkt bitte auf der Karte setzen.") from e
        finally:
            _geo_last[0] = time.time()
    finally:
        lock.release()
    if len(_geo_cache) > 2000:
        _geo_cache.clear()
    _geo_cache[key] = res
    return res


# ---------------------------------------------------------------- Navi-App (Ferrostar)
# Die App schickt eine Valhalla-Anfrage (format=osrm mit Banner-/Sprachanweisungen). Wir lassen
# nur bekannte Felder durch und setzen Profil, Sprache und Einheiten selbst – das Fahrzeug kommt
# aus den URL-Parametern der App.
NAV_KEYS = {"locations", "format", "banner_instructions", "voice_instructions", "directions_options",
            "alternates", "roundabout_exits", "id", "filters", "linear_references"}


def nav_route(body, vehicle, avoid_motorway=False, avoid_zones=None, avoid=None):
    """-> (status, bytes, costing, lez_note). Mit avoid_zones (nur Rig) wird `exclude_polygons` mitgegeben (seit v0.19.0);
    gibt Valhalla damit keine Route, geht die Antwort ohne Ausschluss zurueck und lez_note sagt es."""
    if not isinstance(body, dict):
        raise PlanError("Ungültige Anfrage.")
    locs = body.get("locations")
    if not isinstance(locs, list) or not 2 <= len(locs) <= MAX_POINTS:
        raise PlanError("Start und Ziel fehlen.")
    for loc in locs:
        if not isinstance(loc, dict) or _num(loc.get("lat"), -90, 90, None) is None or _num(loc.get("lon"), -180, 180, None) is None:
            raise PlanError("Ungültiger Punkt.")
    costing, opts = _rig_costing(vehicle, avoid_motorway, avoid)
    req = {k: v for k, v in body.items() if k in NAV_KEYS}
    req.update(costing=costing, costing_options={costing: opts}, units="kilometers", language="de-DE")
    if "alternates" in req:
        req["alternates"] = min(int(_num(req["alternates"], 0, 2, 0)), 2)
    excl, lez_note = [], None
    if avoid_zones:   # kein list -> _avoid ignoriert es
        a, b = locs[0], locs[-1]
        excl, lez_note = _avoid(avoid_zones, (float(a["lat"]), float(a["lon"])), (float(b["lat"]), float(b["lon"])))
        if excl:
            req["exclude_polygons"] = excl
    try:
        r = httpx.post(f"{VALHALLA_URL}/route", json=req, timeout=120)
        if excl and r.status_code != 200:
            log.info("Navi-Route ohne Route mit Umweltzonen-Ausschluss, plane ohne")
            del req["exclude_polygons"]
            lez_note = "; ".join(filter(None, [lez_note, "Umfahren nicht möglich"]))
            r = httpx.post(f"{VALHALLA_URL}/route", json=req, timeout=120)
    except httpx.HTTPError as e:
        raise PlanError("Der Routenplaner ist gerade nicht erreichbar.") from e
    content = r.content
    if r.status_code != 200:
        content = _nav_explain(r, req, costing)
    return r.status_code, content, costing, lez_note


def _nav_explain(r, req, costing):
    """Fehlerantwort fuers Navi um `ra_message` ergaenzen (seit v0.26.3): wie im Planer sagen, ob es am Fahrzeug liegt."""
    try:
        data = r.json()
    except ValueError:
        return r.content
    if not isinstance(data, dict):
        return r.content
    if data.get("error_code") in (170, 171, 442, 443):
        msg = ("Keine Route gefunden. Liegt das Ziel an einer Straße im Planungsgebiet "
               f"({AREA})? Sonst das Ziel etwas verschieben.")
        car = {k: v for k, v in req.items() if k not in ("costing_options", "exclude_polygons")}
        car.update(costing="auto", alternates=0)   # Pkw ohne Hoehe/Breite/Gewicht
        try:
            ok = httpx.post(f"{VALHALLA_URL}/route", json=car, timeout=60).status_code == 200
        except httpx.HTTPError:
            ok = False
        if ok:
            msg = ("Keine Route mit eurem Fahrzeug: Für Pkw geht es, die Zufahrt ist laut Karte für euer Fahrzeug "
                   "gesperrt (Durchfahrtsverbot, Gewicht, Länge oder Höhe). Ziel etwas verschieben oder einen "
                   "anderen Platz wählen.")
        data["ra_message"] = msg
    return json.dumps(data).encode()
