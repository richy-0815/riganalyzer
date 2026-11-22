"""reise.json fuer das RigNavi-Offline-Paket: Etappen, Gespann-Warnungen und Orte an der Strecke
aus Planer- und Analyse-Ergebnis. km-Angaben gelten fuer die ganze Reise (Etappen aufaddiert).
"""

import logging

log = logging.getLogger("reisedaten")

CAMP_GROUP = {"Entsorgung": "dump", "Frischwasser": "water", "Trinkwasser": "water"}


def _stage_ranges(stops):
    """Indizes der Stops, an denen eine Etappe endet (Uebernachtung oder Ziel)."""
    ends = [i for i, s in enumerate(stops) if s.get("stop") and 0 < i < len(stops) - 1]
    return ends + [len(stops) - 1]


def build(trip, plan, analysis):
    stops = trip["stops"]
    stages_a = analysis["stages"] if analysis.get("trip") else [analysis]
    plan_st = plan.get("stages") or []

    stages, start = [], 0
    for n, end in enumerate(_stage_ranges(stops), 1):
        p = plan_st[n - 1] if n - 1 < len(plan_st) else {}
        stages.append({"n": n, "name": f"Etappe {n}: {stops[start]['name']} → {stops[end]['name']}",
                       "from": stops[start]["name"], "to": stops[end]["name"], "km": p.get("km"),
                       "minutes": p.get("min"), "lat": stops[end]["lat"], "lon": stops[end]["lon"],
                       "mode": stops[end].get("mode"),   # Autobahn-Einstellung des Abschnitts bis zum Etappenziel
                       "via": [{"name": s["name"], "lat": s["lat"], "lon": s["lon"], "mode": s.get("mode"),
                                **({"stay": s["stay"]} if s.get("stay") else {})}   # Aufenthalt in min
                               for s in stops[start + 1:end]]})
        start = end

    warnings, pois, countries, offset, line = [], [], {}, 0.0, []
    lez, lez_known = {}, False
    for st in stages_a:
        rig = st.get("rig") or {}
        for r in rig.get("restrictions", []):
            warnings.append({"type": "restriction", "kind": r["kind"], "value": r.get("value"), "label": r["label"],
                             "name": r.get("name", ""), "lat": r["lat"], "lon": r["lon"],
                             "km0": round(r["km0"] + offset, 2), "km1": round(r["km1"] + offset, 2)})
        if rig.get("lez") is not None:
            lez_known = True
            for z in rig["lez"]:
                lez.setdefault(z["id"], {k: z.get(k) for k in ("id", "name", "country", "rule", "since", "until", "poly")})
        for s in rig.get("steep", []):
            warnings.append({"type": "steep", "kind": s["dir"], "value": s["max"],
                             "label": f"{'Gefälle' if s['dir'] == 'down' else 'Steigung'} bis {s['max']} %",
                             "lat": s["lat"], "lon": s["lon"],
                             "km0": round(s["km0"] + offset, 2), "km1": round(s["km1"] + offset, 2)})
        for h in rig.get("hairpins", []):
            warnings.append({"type": "hairpin", "kind": h["cls"], "value": h["r"], "label": f"Kehre, Radius {h['r']} m",
                             "lat": h["lat"], "lon": h["lon"],
                             "km0": round(h["km"] + offset, 2), "km1": round(h["km"] + offset, 2)})
        for p in st.get("pois", []):
            if p["group"] == "fuel":
                # Abstecher hinter Ausfahrten und Tankkarten-Stationen kennt RigNavi nicht: nicht ins Paket
                if (p.get("flags") or {}).get("detour_km") or (p.get("flags") or {}).get("card_only"):
                    continue
                g = "fuel"
            elif p["group"] == "camp":
                g = CAMP_GROUP.get(p["label"], "camp")
            elif p["group"] == "shop":
                g = "shop"
            else:
                continue
            flags = p.get("flags") or {}
            pois.append({"group": g, "label": p["label"], "name": p.get("name") or "", "lat": p["lat"], "lon": p["lon"],
                         "km": round(p["km"] + offset, 2), "motorway": bool(g == "fuel" and flags.get("motorway")),
                         "flags": flags, "water_kind": p.get("water_kind"), "near_stage": p.get("near_stage"),
                         "osm": p.get("osm")})   # Schluessel fuer eigene Bewertungen
        for c in rig.get("countries", []):
            e = countries.setdefault(c["cc"], {"cc": c["cc"], "name": c["name"], "km": 0, "motorway_km": 0})
            e["km"] += c["km"]
            e["motorway_km"] += c.get("motorway_km", 0)
        # ausgeduennte Linie fuer die Plaetze der Tourismusaemter: etwa alle 1,5 km ein Punkt
        for g in st.get("segments", []):
            c, length = g.get("c") or [], float(g.get("km1", 0)) - float(g.get("km0", 0))
            step = max(1, round(len(c) / max(1.0, length / 1.5)))
            for i in range(0, len(c), step):
                line.append((c[i][0], c[i][1], float(g.get("km0", 0)) + offset + length * i / max(1, len(c) - 1)))
        offset += float((st.get("stats") or {}).get("km") or 0)

    warnings.sort(key=lambda w: w["km0"])
    pois.sort(key=lambda p: p["km"])
    # Platz am Nacht-Punkt liegt im 3-km-Streifen beider Etappen: nur einmal (der erste) ins Paket
    seen, uniq = set(), []
    for p in pois:
        k = (p["group"], p["label"], round(p["lat"], 6), round(p["lon"], 6))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    pois = uniq
    _add_fr(pois, line)
    pois = sorted(pois + way_pois(stages_a), key=lambda p: p["km"])
    return {"name": trip["name"], "vehicle": trip["vehicle"], "stages": stages, "warnings": warnings,
            "pause": trip.get("pause") or "auto",
            "pois": pois, "lez": list(lez.values()) if lez_known else None, "countries": sorted(countries.values(), key=lambda c: -c["km"])}


# Unterwegs-Orte fuers Navi: wie der Reiter „🧭 Unterwegs“; je Etappe und Gruppe hoechstens
# WAY_MAX (Sehenswertes nach Bedeutung), damit das Paket klein bleibt. Rastplaetze/Picknick bleiben draussen.
WAY_GROUPS = ("sight", "food", "service")
WAY_MAX = 300
WAY_FLAGS = ("hours", "kind", "brand", "website", "phone")


def way_pois(stages_a):
    """Sehenswertes, Einkehr, Service aus den Analyse-Etappen -> Paket-Orte (km ueber die ganze Reise), nach km."""
    out, offset = [], 0.0
    for st in stages_a:
        for g in WAY_GROUPS:
            ps = [p for p in st.get("pois", []) if p.get("group") == g]
            if len(ps) > WAY_MAX:
                ps = sorted(ps, key=lambda p: -float(p.get("score") or 0))[:WAY_MAX]
            for p in ps:
                flags = p.get("flags") or {}
                out.append({"group": g, "label": p.get("label") or "", "name": p.get("name") or "",
                            "lat": p["lat"], "lon": p["lon"], "km": round(p["km"] + offset, 2), "off": p.get("off"),
                            "motorway": False,
                            "score": p.get("score"), "wiki": bool(p.get("wiki")), "osm": p.get("osm"),
                            "flags": {k: flags[k] for k in WAY_FLAGS if flags.get(k) is not None}})
        offset += float((st.get("stats") or {}).get("km") or 0)
    return sorted(out, key=lambda p: p["km"])


FR_KEYS = ("n", "k", "a", "u", "w", "r", "s", "d")


def _add_fr(pois, line):
    """Angaben der franzoesischen Tourismusaemter (DATAtourisme, 0.24.0) an die Plaetze haengen, fehlende Plaetze
    entlang der Route dazunehmen. Ohne Daten (noch nicht geladen) oder bei Fehlern: Paket bleibt wie es ist."""
    try:
        import frcamps
        cps = [(str(i), p["lat"], p["lon"], p["name"]) for i, p in enumerate(pois) if p["group"] == "camp"]
        res = frcamps.lookup(cps, line[:5000])
    except Exception:   # Zusatz, darf das Paket nie verhindern
        log.exception("Tourismusamt-Angaben fuers Paket fehlgeschlagen")
        return
    for i, v in res["match"].items():
        pois[int(i)]["fr"] = {k: v.get(k) for k in FR_KEYS}
    for e in res["extra"]:
        pois.append({"group": "camp", "label": "Campingplatz" if e["k"] == "camping" else "Wohnmobil-Stellplatz",
                     "name": e["n"], "lat": e["lat"], "lon": e["lon"], "km": e["km"], "motorway": False,
                     "flags": {"kind": "camp_site" if e["k"] == "camping" else "caravan_site"},
                     "water_kind": None, "near_stage": None, "fr": {**{k: e.get(k) for k in FR_KEYS}, "only": True},
                     "osm": f"dt:{e['id']}" if e.get("id") else None})
    pois.sort(key=lambda p: p["km"])
