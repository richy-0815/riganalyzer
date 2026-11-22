"""RigAnalyzer – was ein Wohnmobil/Gespann auf der Route beschaeftigt.

Wird von analyze.analyze() mit dessen Zwischenergebnissen aufgerufen und liefert
Rohdaten: Beschraenkungen (Hoehe, Breite, Gewicht, Laenge, Verbote) mit Zahlenwert,
Engstellen, Hoehenprofil + Steigungen, Laender (Maut/Vignette), Fahrtempo je
Abschnitt fuer schwere Fahrzeuge. Die Bewertung gegen die Fahrzeugdaten (Hoehe 3,2 m
usw.) macht der Browser – so wirken Einstellungen sofort, ohne neue Analyse.
"""

import logging
import math
import re

import numpy as np

import dem
import sondermaut
from poi import (  # noqa: F401 – im Kern (poi.py); Namen hier fuer bestehende Aufrufer
    CLOSED_ACCESS, DISCOUNT_BRANDS, MAJOR_BRANDS, _tonnes, _yesno, fuel_class, measure, poi_flags, water_kind)


log = logging.getLogger("rig")


COUNTRY = {  # Geofabrik-Pfadteil -> (Kuerzel, Name)
    "germany": ("DE", "Deutschland"), "austria": ("AT", "Österreich"), "slovenia": ("SI", "Slowenien"),
    "switzerland": ("CH", "Schweiz"), "italy": ("IT", "Italien"), "france": ("FR", "Frankreich"),
    "czech-republic": ("CZ", "Tschechien"), "poland": ("PL", "Polen"), "netherlands": ("NL", "Niederlande"),
    "belgium": ("BE", "Belgien"), "denmark": ("DK", "Dänemark"), "croatia": ("HR", "Kroatien"),
    "hungary": ("HU", "Ungarn"), "slovakia": ("SK", "Slowakei"), "luxembourg": ("LU", "Luxemburg"),
    "liechtenstein": ("LI", "Liechtenstein"), "spain": ("ES", "Spanien"), "portugal": ("PT", "Portugal"),
    "norway": ("NO", "Norwegen"), "sweden": ("SE", "Schweden"),
}

ACCESS_BAD = {"no", "private"}
ACCESS_SOFT = {"destination", "delivery", "customers", "agricultural", "forestry", "permit"}


# --------------------------------------------------------------------------- Werte lesen

def is_oneway(tg):
    """Einbahn – auch implizit: Kreisverkehre und Autobahnen tragen meist kein oneway-Tag."""
    return (tg.get("oneway") in ("yes", "-1", "1", "true")
            or tg.get("junction") in ("roundabout", "circular")
            or tg.get("highway") in ("motorway", "motorway_link"))


def _min(*vals):
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def way_items(tg):
    """Beschraenkungen eines OSM-Wegs -> [(kind, value, raw, label)]."""
    out = []
    h = _min(measure(tg.get("maxheight")), measure(tg.get("maxheight:physical")))
    if h is not None:
        out.append(("height", h, tg.get("maxheight") or tg.get("maxheight:physical"), "Durchfahrtshöhe"))
    elif tg.get("tunnel") in ("yes", "building_passage", "culvert"):
        out.append(("tunnel", None, "", "Tunnel/Unterführung ohne Höhenangabe"))
    w = _min(measure(tg.get("maxwidth")), measure(tg.get("maxwidth:physical")))
    if w is not None:
        out.append(("width", w, tg.get("maxwidth") or tg.get("maxwidth:physical"), "Breitenbeschränkung"))
    wt = _min(measure(tg.get("maxweight")), measure(tg.get("maxweightrating")))
    if wt is not None:
        out.append(("weight", wt, tg.get("maxweight") or tg.get("maxweightrating"), "Gewichtsbeschränkung"))
    wh = measure(tg.get("maxweight:hgv"))
    if wh is not None:
        out.append(("weight_hgv", wh, tg.get("maxweight:hgv"), "Gewichtsbeschränkung für Lkw"))
    ax = measure(tg.get("maxaxleload"))
    if ax is not None:
        out.append(("axle", ax, tg.get("maxaxleload"), "Achslastbeschränkung"))
    ln = measure(tg.get("maxlength"))
    if ln is not None:
        out.append(("length", ln, tg.get("maxlength"), "Längenbeschränkung"))
    for key, label in (("motor_vehicle", "Kraftfahrzeuge"), ("motorcar", "Autos"), ("vehicle", "Fahrzeuge"),
                       ("access", "Zufahrt")):
        v = tg.get(key)
        if v in ACCESS_BAD:
            out.append(("closed", None, f"{key}={v}", f"Verbot für {label}" if key != "access" else "Zufahrt verboten"))
            break
        if v in ACCESS_SOFT:
            out.append(("destination", None, f"{key}={v}", "Nur Anlieger/Zufahrt frei"))
            break
    if tg.get("hgv") in ACCESS_BAD | ACCESS_SOFT:
        out.append(("hgv", None, f"hgv={tg['hgv']}", "Verbot für Lkw / über 3,5 t" if tg["hgv"] in ACCESS_BAD
                    else "Lkw / über 3,5 t nur Anlieger"))
    if tg.get("trailer") in ACCESS_BAD:
        out.append(("trailer", None, "trailer=no", "Verbot für Anhänger"))
    if tg.get("caravan") in ACCESS_BAD:
        out.append(("caravan", None, "caravan=no", "Verbot für Wohnwagen"))
    if tg.get("motorhome") in ACCESS_BAD:
        out.append(("motorhome", None, "motorhome=no", "Verbot für Wohnmobile"))
    rw = _min(measure(tg.get("width")), measure(tg.get("est_width")))
    one = is_oneway(tg)
    # Gegenverkehr braucht ~5,5 m; eine Einbahnspur (Kreisel, Auffahrt) ist mit 3,5 m normal breit
    if rw is not None and rw < (3.5 if one else 5.5):
        out.append(("roadwidth", rw, tg.get("width") or tg.get("est_width"), "Schmale Straße"))
    elif tg.get("narrow") == "yes":
        out.append(("roadwidth", None, "narrow=yes", "Schmale Straße"))
    if not one and (tg.get("passing_places") == "yes" or tg.get("lanes") == "1"):
        out.append(("singlelane", None, "", "Einspurig mit Gegenverkehr"))
    inc = tg.get("incline")
    if inc:
        m = re.match(r"^-?(\d+(?:[.,]\d+)?)\s*%", str(inc))
        if m:
            out.append(("incline", float(m.group(1).replace(",", ".")), inc, "Steigung laut Karte"))
    if tg.get("toll") == "yes" or tg.get("toll:hgv") == "yes":
        out.append(("toll", None, "toll", "Mautpflichtig"))
    return out


def node_items(tg):
    out = []
    h = _min(measure(tg.get("maxheight")), measure(tg.get("maxheight:physical")))
    if h is not None:
        out.append(("height", h, tg.get("maxheight") or tg.get("maxheight:physical"),
                    "Höhenbegrenzer" if tg.get("barrier") == "height_restrictor" else "Durchfahrtshöhe"))
    elif tg.get("barrier") == "height_restrictor":
        out.append(("height", None, "", "Höhenbegrenzer (Höhe unbekannt)"))
    w = _min(measure(tg.get("maxwidth")), measure(tg.get("maxwidth:physical")))
    if w is not None:
        out.append(("width", w, tg.get("maxwidth") or tg.get("maxwidth:physical"), "Engstelle (Breite)"))
    wt = measure(tg.get("maxweight"))
    if wt is not None:
        out.append(("weight", wt, tg.get("maxweight"), "Gewichtsbeschränkung"))
    return out


# --------------------------------------------------------------------------- Hoehen

def elevation(lat, lon, progress=lambda p, m: None):
    """Hoehen (m) aus dem lokalen Copernicus-DEM (Kacheln werden bei Bedarf geladen)."""
    return dem.sample(lat, lon)


# --------------------------------------------------------------------------- Hauptteil

def collect(ctx, want_elevation=True, progress=lambda p, m: None):
    """ctx: Zwischenergebnisse aus analyze() (siehe dort). -> dict fuer result["rig"]."""
    S, plat, plon, N = ctx["S"], ctx["plat"], ctx["plon"], ctx["N"]
    ways, match = ctx["ways"], ctx["match"]
    STEP = ctx["STEP"]
    def km(i):
        return round(float(S[i]) / 1000, 2)

    # ---- Beschraenkungen entlang der Wege (zusammenhaengende Stuecke)
    cache = {}
    items = []
    open_ = {}   # (kind, value, label) -> [start_i, last_i, raw, name]
    for i in range(N):
        m = int(match[i])
        cur = set()
        if m >= 0:
            if m not in cache:
                tg = ways[m].get("tags", {})
                cache[m] = (way_items(tg), tg.get("ref") or tg.get("name") or "")
            its, nm = cache[m]
            for kind, val, raw, label in its:
                k = (kind, val, label)
                cur.add(k)
                if k in open_ and i - open_[k][1] <= 3:
                    open_[k][1] = i
                else:
                    if k in open_:
                        items.append((k, *open_[k]))
                    open_[k] = [i, i, raw, nm]
        for k in [k for k in open_ if k not in cur and i - open_[k][1] > 3]:
            items.append((k, *open_.pop(k)))
    items += [(k, *v) for k, v in open_.items()]
    restrictions = []
    for (kind, val, label), a, b, raw, nm in items:
        mid = (a + b) // 2
        restrictions.append({"kind": kind, "value": val, "raw": raw, "label": label, "name": nm,
                             "km0": km(a), "km1": km(b), "len": round((b - a) * STEP),
                             "lat": round(float(plat[mid]), 5), "lon": round(float(plon[mid]), 5), "src": "way"})

    # ---- Beschraenkungen an Knoten (Hoehenbegrenzer, Engstellen)
    for nid, el in ctx["nodes"].items():
        its = node_items(el.get("tags", {}))
        if not its or nid not in ctx["node_xy"]:
            continue
        x, y = ctx["node_xy"][nid]
        i = ctx["nearest_idx"](x, y, 15.0)
        if i is None:
            continue
        here = set(int(m) for m in match[max(i - 2, 0):i + 3] if m >= 0)
        if not (ctx["node_ways"][nid] & here):
            continue
        for kind, val, raw, label in its:
            restrictions.append({"kind": kind, "value": val, "raw": raw, "label": label, "name": "",
                                 "km0": km(i), "km1": km(i), "len": 0,
                                 "lat": round(float(plat[i]), 5), "lon": round(float(plon[i]), 5), "src": "node"})
    restrictions.sort(key=lambda r: r["km0"])

    # ---- Kehren (Gespann-relevant: Radius)
    hairpins = [{"km": km(c["i"]), "r": c["r"], "angle": c["angle"], "cls": c["cls"],
                 "lat": round(float(plat[c["i"]]), 5), "lon": round(float(plon[c["i"]]), 5)}
                for c in ctx["curves"] if c["cls"] in ("kehre", "eng") and c["r"] < 35]

    # ---- Hoehenprofil + Steigungen (Bruecken/Tunnel ueberbruecken: DEM zeigt dort Tal/Berg)
    idx = np.arange(0, N, 10)        # alle 100 m
    if idx[-1] != N - 1:
        idx = np.append(idx, N - 1)
    ele = None
    steep, profile = [], []
    asc = desc = None
    if want_elevation:
        try:
            progress(None, "Höhenprofil wird geladen")
            ele = elevation(plat[idx], plon[idx], progress)
        except Exception as e:  # noqa: BLE001 – Hoehenprofil ist Beiwerk, Analyse laeuft ohne
            log.warning("Hoehenprofil nicht verfuegbar: %s", e)
            ele = None
    grade_pt = np.zeros(N)
    if ele is not None:
        art = np.zeros(len(idx), bool)
        for k, i in enumerate(idx):
            m = int(match[i])
            if m >= 0:
                tg = ways[m].get("tags", {})
                art[k] = tg.get("bridge") not in (None, "no") or tg.get("tunnel") not in (None, "no")
        good = ~art
        if good.sum() >= 2:
            ele = np.interp(S[idx], S[idx][good], ele[good])
        # Copernicus ist ein Oberflaechenmodell (Baumkronen, Gebaeude): Wald/Lichtung-Wechsel
        # erzeugen 20-30-m-Spruenge. Daher erst Median ueber 500 m (wirft Ausreisser raus),
        # dann Mittel ueber 300 m; Steigung als Differenz ueber 400 m.
        pad = np.pad(ele, 2, mode="edge")
        med = np.median(np.lib.stride_tricks.sliding_window_view(pad, 5), axis=1)
        ek = np.convolve(np.pad(med, 1, mode="edge"), np.ones(3) / 3, mode="valid")
        sk = S[idx].astype(float)
        e2, s2 = np.pad(ek, 2, mode="edge"), np.pad(sk, 2, mode="edge")
        gk = (e2[4:] - e2[:-4]) / np.maximum(s2[4:] - s2[:-4], 50.0)   # je Stichprobe, ueber +-200 m
        g = (gk[:-1] + gk[1:]) / 2                                   # je Intervall
        asc = int(np.clip(np.diff(ek), 0, None).sum())
        desc = int(np.clip(-np.diff(ek), 0, None).sum())
        for k in range(len(g)):
            grade_pt[idx[k]:idx[k + 1] + 1] = g[k]
        for sign in (1, -1):
            mask = (g * sign) >= 0.07
            m2 = np.concatenate([[False], mask, [False]]).astype(int)
            starts, ends = np.where(np.diff(m2) == 1)[0], np.where(np.diff(m2) == -1)[0]
            for a, b in zip(starts, ends):
                length = float(S[idx[b]] - S[idx[a]])
                if length < 250:
                    continue
                seg = g[a:b] * sign
                mid = idx[(a + b) // 2]
                steep.append({"km0": km(idx[a]), "km1": km(idx[b]), "len": round(length),
                              "max": round(float(seg.max()) * 100, 1), "avg": round(float(seg.mean()) * 100, 1),
                              "dir": "up" if sign > 0 else "down",
                              "ele0": int(ek[a]), "ele1": int(ek[b]),
                              "lat": round(float(plat[mid]), 5), "lon": round(float(plon[mid]), 5)})
        steep.sort(key=lambda s: s["km0"])
        step = max(1, len(idx) // 500)
        gk_pct = np.clip(gk * 100, -30, 30)
        profile = [[km(idx[k]), int(ek[k]), round(float(gk_pct[k]), 1)] for k in range(0, len(idx), step)]

    # ---- Fahrtempo fuer schwere Fahrzeuge je Abschnitt (ohne Fahrzeug-Hoechstgeschwindigkeit;
    #      die setzt der Browser): Kurven gemuetlich (1,5 m/s²), traege beschleunigen, bergauf langsamer
    kappa, vlim = ctx["kappa"], ctx["vlim"]
    v = np.minimum(vlim, np.sqrt(1.5 / np.maximum(np.abs(kappa), 1e-5)) * 3.6)
    up = np.clip(grade_pt, 0, None)
    v = np.minimum(v, np.where(up > 0.04, 95 - 450 * up, 200))
    v = np.clip(v, 5, 130) / 3.6
    for i in range(1, N):
        v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * 0.6 * STEP))
    for i in range(N - 2, -1, -1):
        v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * 1.5 * STEP))
    seg_rig = []
    for a, b in ctx["bounds"]:
        b = max(b, a + 1)
        t = float(np.sum(STEP / v[a:b]))
        gseg = grade_pt[a:b + 1]
        seg_rig.append({"rv": round((S[b] - S[a]) / max(t, 1e-6) * 3.6, 1),
                        "g": round(float(np.max(np.abs(gseg))) * 100, 1) if ele is not None else None,
                        "up": round(float(np.max(gseg)) * 100, 1) if ele is not None else None})

    # ---- Laender (fuer Maut/Vignette): je Stichprobe die Region der DB bzw. des Katalogs
    countries = {}
    cls = ctx["cls"]
    ways, match = ctx.get("ways"), ctx.get("match")
    region_of = ctx.get("region_of")
    if region_of:
        sel = np.arange(0, N, 50)   # alle 500 m
        regs = region_of(plat[sel], plon[sel])
        for k, i in enumerate(sel):
            r = regs[k]
            part = r.split("/")[1] if r and r.count("/") >= 1 else None
            cc, name = COUNTRY.get(part, (part.upper()[:2] if part else "?", part or "unbekannt"))
            e = countries.setdefault(cc, {"cc": cc, "name": name, "km": 0.0, "motorway_km": 0.0, "toll_km": 0.0})
            e["km"] += 0.5
            if cls[i] in ("motorway", "trunk"):
                e["motorway_km"] += 0.5
            # Maut-km: Strasse mit toll=yes (Pkw), Grundlage der Mautrechnung je Land
            if ways is not None and match is not None and 0 <= match[i] < len(ways):
                if (ways[match[i]].get("tags") or {}).get("toll") == "yes":
                    e["toll_km"] += 0.5
    country_list = sorted(({**c, "km": round(c["km"]), "motorway_km": round(c["motorway_km"]), "toll_km": round(c["toll_km"])}
                           for c in countries.values()), key=lambda c: -c["km"])

    # ---- Sondermaut: Tunnel/Bruecken mit eigenem Tarif, Preis rechnet der Browser (toll.js)
    def way_tags(i):
        if ways is None or match is None or not 0 <= match[i] < len(ways):
            return None
        return ways[match[i]].get("tags") or {}
    special = sondermaut.detect(plat, plon, S, cls, way_tags)

    return {"restrictions": restrictions, "hairpins": hairpins, "steep": steep, "profile": profile,
            "ascent": asc, "descent": desc, "countries": country_list, "segments": seg_rig, "special": special,
            "elevation": ele is not None}


def fits_rig(flags, veh):
    """Passt das Fahrzeug laut Platzangaben? ok / unknown (Angaben fehlen) / no.
    Seit 0.24.0 auch: Hoehe (maxheight, z. B. Schranke/Tor) und „motorhome=no“ (Wohnmobile nicht erlaubt)."""
    ml, mw, mh = flags.get("maxlength"), flags.get("maxweight"), flags.get("maxheight")
    if (ml is not None and veh.get("length") and veh["length"] > ml) or \
            (mw is not None and veh.get("weight") and veh["weight"] > mw) or \
            (mh is not None and veh.get("height") and veh["height"] > mh) or flags.get("motorhome") is False:
        return "no"
    return "unknown" if ml is None or mw is None else "ok"
