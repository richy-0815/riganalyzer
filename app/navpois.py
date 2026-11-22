"""Tankstellen und Stell-/Campingplaetze entlang einer Navi-Route (seit v0.27.0).

Fuer die iPad-App, die (anders als RigNavi auf Android) keine Offline-Pakete hat: sie schickt die Linie der
gerade berechneten Route und bekommt aus den lokalen OSM-Daten, was daran liegt – mit km ab Start, damit die App
„naechste Tankstelle in 34 km“ zeigen kann. Preise holt sie danach wie Android ueber /api/fuel/prices.
"""

import logging

import numpy as np

import nights
import poi

log = logging.getLogger("navpois")

MAX_POINTS = 20000
FUEL_RADIUS_M = 500        # wie poi_radius fuer Tankstellen: kein grosser Umweg
CAMP_RADIUS_M = 3000       # Stellplatz darf etwas abseits liegen
STEP_M = 150               # Suchpunkte entlang der Linie
MAX_FUEL, MAX_CAMPS = 400, 300


class NavPoiError(ValueError):
    pass


def _thin(lat, lon, km, step_m=STEP_M):
    """Etwa alle step_m einen Punkt (Valhalla-Shapes sind in Staedten sehr dicht)."""
    keep, last = [0], 0.0
    for i in range(1, len(km)):
        if (km[i] - last) * 1000 >= step_m:
            keep.append(i)
            last = km[i]
    if keep[-1] != len(km) - 1:
        keep.append(len(km) - 1)
    k = np.asarray(keep)
    return lat[k], lon[k], km[k]


def _along(lat, lon, la, lo, proj, km):
    """km ab Start fuer einen Punkt: naechster Linienpunkt."""
    x, y = proj.fwd(lat, lon)
    px, py = proj.fwd(la, lo)
    return float(km[int(np.argmin(np.hypot(x - px, y - py)))])


def find(shape, vehicle, db):
    """shape [[lat, lon], …] -> {"fuel": […], "camps": […], "data": bool}. Ohne OSM-Daten leere Listen."""
    try:
        pts = np.asarray([[float(a), float(b)] for a, b in shape], float)
    except (TypeError, ValueError):
        raise NavPoiError("Ungültige Linie.") from None
    if len(pts) < 2 or len(pts) > MAX_POINTS:
        raise NavPoiError("Linie fehlt oder ist zu lang.")
    if np.any(np.abs(pts[:, 0]) > 90) or np.any(np.abs(pts[:, 1]) > 180):
        raise NavPoiError("Ungültige Linie.")
    lat, lon = pts[:, 0], pts[:, 1]
    proj = nights._Proj(float(lat.mean()), float(lon.mean()))
    x, y = proj.fwd(lat, lon)
    km = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))]) / 1000
    if db is None:
        return {"fuel": [], "camps": [], "data": False}
    tl, to, tk = _thin(lat, lon, km)
    vehicle = vehicle or {}

    fuel = []
    try:
        found = db.fuel_near(tl, to, proj, FUEL_RADIUS_M)
    except Exception as e:      # noqa: BLE001 – Beiwerk: lieber leer als Fehler
        log.warning("Tankstellensuche fehlgeschlagen: %s", e)
        found = []
    for el, dist in found:
        tg = el["tags"]
        flags = poi.poi_flags(tg, "fuel")
        if not flags["diesel"] or flags["card_only"]:
            continue
        la, lo = (el["lat"], el["lon"]) if "lat" in el else (el["center"]["lat"], el["center"]["lon"])
        fuel.append({"name": tg.get("name") or tg.get("brand") or "Tankstelle", "brand": flags["brand"],
                     "lat": round(la, 6), "lon": round(lo, 6), "km": round(_along(tl, to, la, lo, proj, tk), 2),
                     "off_m": round(dist), "truck": flags["truck"], "adblue": flags["adblue"], "hours": flags["hours"],
                     "osm": f'{el["type"]}/{el["id"]}'})
    fuel.sort(key=lambda f: f["km"])

    camps = []
    try:
        found = db.camps_near(tl, to, proj, CAMP_RADIUS_M)
    except Exception as e:  # noqa: BLE001 – Beiwerk: lieber leer als Fehler
        log.warning("Platzsuche fehlgeschlagen: %s", e)
        found = []
    for el, dist in found:
        tg = el["tags"]
        if not nights._usable(tg):
            continue
        flags = poi.poi_flags(tg, "camp")
        fits = nights._fits(flags, tg, vehicle)
        la, lo = (el["lat"], el["lon"]) if "lat" in el else (el["center"]["lat"], el["center"]["lon"])
        camps.append({"name": tg.get("name") or ("Wohnmobil-Stellplatz" if tg.get("tourism") == "caravan_site" else "Campingplatz"),
                      "named": bool(tg.get("name")), "kind": tg.get("tourism"), "lat": round(la, 6), "lon": round(lo, 6),
                      "km": round(_along(tl, to, la, lo, proj, tk), 2), "off_km": round(dist / 1000, 1), "fits": fits,
                      "flags": {k: v for k, v in flags.items() if v not in (None, "")}, "osm": f'{el["type"]}/{el["id"]}'})
    camps.sort(key=lambda c: c["km"])
    return {"fuel": fuel[:MAX_FUEL], "camps": camps[:MAX_CAMPS], "data": bool(db.covers(tl, to))}
