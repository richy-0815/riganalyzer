"""RideAnalyzer – Analyse einer GPX-Route gegen OpenStreetMap.

Ablauf: GPX -> 10-m-Raster -> OSM-Daten (Overpass) -> Map-Matching -> Ereignisse
(Ampeln, Wartepflicht, Kreisverkehre ...) -> Kurven -> Orte -> Landschaft ->
POIs -> Abschnitte mit Achsen-Scores -> Kennzahlen + Highlights.

Alle Scores 0..100, hoeher = "mehr davon, was die Achse beschreibt" (Fahrfluss,
Kurven, Belagkomfort, Landschaft, kleine Strassen, wenig Ort). Die Gewichtung
nach Fahrerprofil passiert im Browser.
"""

from __future__ import annotations

import heapq
import logging
import math
import re
from collections import Counter, defaultdict

import gpxpy
import numpy as np
import shapely
from shapely import STRtree, points as shp_points
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import linemerge, polygonize, unary_union

import osmdb
import regions
import lez
import overpass
import poi
import hooks

STEP = 10.0            # Raster in m
SEG_LEN = 500.0        # Abschnittslaenge fuer Karte/Band in m
MAX_KM = float(__import__("os").environ.get("MAX_KM", "1000"))
MATCH_RADIUS = 25.0
SPARSE_M = 60.0        # Median-Punktabstand, ab dem eine Route als "ausgeduennt" gilt
RECON_RADIUS = 150.0   # Suchkorridor fuer die Rekonstruktion
CHUNK_M = 20000.0      # Overpass-Teilstueck
PLACE_RANK = {"city": 0, "town": 1, "village": 2}   # Orte an der Strecke, kleiner = wichtiger
PLACE_NEAR_M = 2000.0
PLACE_MAX = 3000

ROAD_EXCLUDE = ("footway|path|cycleway|steps|pedestrian|bridleway|corridor|elevator|platform|"
                "proposed|construction|bus_stop|via_ferrata|raceway|abandoned|razed|services|rest_area")

RANK = {"motorway": 7, "trunk": 6, "primary": 5, "secondary": 4, "tertiary": 3,
        "unclassified": 2, "residential": 1, "living_street": 0, "service": 0,
        "track": 0, "road": 1}

ROAD_LABEL = {"motorway": "Autobahn", "trunk": "Schnellstraße", "primary": "Bundesstraße",
              "secondary": "Landesstraße", "tertiary": "Kreisstraße",
              "unclassified": "Nebenstraße", "residential": "Wohnstraße",
              "living_street": "Spielstraße", "service": "Zufahrt", "track": "Wirtschaftsweg",
              "road": "Straße", "?": "unbekannt"}
# "Kleine, feine Strasse" – Strassencharakter-Score je Klasse
ROAD_SCORE = {"motorway": 10, "trunk": 25, "primary": 45, "secondary": 70, "tertiary": 90,
              "unclassified": 92, "residential": 35, "living_street": 20, "service": 30,
              "track": 25, "road": 50, "?": 50}
# Praktische Fahrgeschwindigkeit je Klasse ausserorts (km/h), gedeckelt durch maxspeed
ROAD_VMAX = {"motorway": 125, "trunk": 100, "primary": 95, "secondary": 90, "tertiary": 85,
             "unclassified": 70, "residential": 35, "living_street": 7, "service": 20,
             "track": 15, "road": 60, "?": 70}

SURFACE_SCORE = {"asphalt": 100, "paved": 90, "concrete": 85, "concrete:plates": 65,
                 "concrete:lanes": 55, "chipseal": 80, "paving_stones": 60, "sett": 35,
                 "unhewn_cobblestone": 15, "cobblestone": 25, "metal": 50, "wood": 40,
                 "compacted": 40, "fine_gravel": 30, "gravel": 20, "pebblestone": 15,
                 "unpaved": 15, "ground": 10, "dirt": 10, "earth": 10, "grass": 5, "sand": 5,
                 "mud": 0, "grass_paver": 20}
SURFACE_LABEL = {"asphalt": "Asphalt", "paved": "befestigt", "concrete": "Beton",
                 "concrete:plates": "Betonplatten", "concrete:lanes": "Betonspur",
                 "paving_stones": "Pflaster (glatt)", "sett": "Kopfsteinpflaster",
                 "cobblestone": "Kopfsteinpflaster", "unhewn_cobblestone": "Feldsteinpflaster",
                 "compacted": "verdichtet", "gravel": "Schotter", "fine_gravel": "Feinschotter",
                 "unpaved": "unbefestigt", "ground": "Erdweg", "dirt": "Erdweg", "sand": "Sand",
                 "chipseal": "Oberflächenbehandlung"}
SMOOTHNESS_FACTOR = {"excellent": 1.0, "good": 1.0, "intermediate": 0.8, "bad": 0.55,
                     "very_bad": 0.35, "horrible": 0.2, "very_horrible": 0.1, "impassable": 0}

# Flussereignisse: Gewicht = wie stark der Rhythmus bricht, Zeit = mittlere Wartezeit (s)
EVENT = {
    "signal":   ("Ampel", 1.0, 25),
    "stop":     ("Stoppschild", 1.0, 8),
    "give_way": ("Vorfahrt gewähren", 0.7, 5),
    "yield_implicit": ("Einmündung in größere Straße", 0.6, 5),
    "roundabout": ("Kreisverkehr", 0.5, 4),
    "level_crossing": ("Bahnübergang", 0.3, 4),
    "turn": ("Abbiegen", 0.3, 3),
    "kehre": ("Spitzkehre", 0.25, 0),   # Zeit steckt im Geschwindigkeitsmodell
}

LAND_CLASS = {
    "forest": "wald", "wood": "wald", "scrub": "wald", "orchard": "wiese", "vineyard": "wiese",
    "water": "wasser", "wetland": "wasser", "beach": "wasser", "reservoir": "wasser",
    "basin": "wasser",
    "farmland": "feld", "meadow": "wiese", "grass": "wiese", "grassland": "wiese",
    "heath": "wiese", "allotments": "siedlung", "farmyard": "siedlung",
    "residential": "siedlung", "industrial": "gewerbe", "commercial": "gewerbe",
    "retail": "gewerbe", "quarry": "gewerbe", "landfill": "gewerbe",
}
LAND_PRIO = {"wasser": 5, "gewerbe": 4, "siedlung": 3, "wald": 2, "wiese": 1, "feld": 1}
LAND_VALUE = {"wald": 1.0, "wasser": 1.0, "wiese": 0.85, "feld": 0.7, "siedlung": 0.15,
              "gewerbe": 0.0}

POI_KIND = {
    ("tourism", "viewpoint"): ("Aussichtspunkt", 3.0, "sight"),
    ("tourism", "attraction"): ("Sehenswürdigkeit", 2.0, "sight"),
    ("tourism", "museum"): ("Museum", 1.5, "sight"),
    ("tourism", "picnic_site"): ("Picknickplatz", 0.5, "pause"),
    ("tourism", "zoo"): ("Tierpark", 1.0, "sight"),
    ("historic", "castle"): ("Burg/Schloss", 3.0, "sight"),
    ("historic", "ruins"): ("Ruine", 2.0, "sight"),
    ("historic", "monument"): ("Denkmal", 1.0, "sight"),
    ("historic", "manor"): ("Gutshaus", 1.5, "sight"),
    ("historic", "fort"): ("Festung", 2.0, "sight"),
    ("historic", "city_gate"): ("Stadttor", 1.5, "sight"),
    ("historic", "monastery"): ("Kloster", 2.5, "sight"),
    ("historic", "archaeological_site"): ("Bodendenkmal", 1.0, "sight"),
    ("historic", "windmill"): ("Windmühle", 1.5, "sight"),
    ("historic", "watermill"): ("Wassermühle", 1.5, "sight"),
    ("man_made", "tower"): ("Aussichtsturm", 3.0, "sight"),
    ("man_made", "lighthouse"): ("Leuchtturm", 2.0, "sight"),
    ("man_made", "windmill"): ("Windmühle", 1.5, "sight"),
    ("man_made", "watermill"): ("Wassermühle", 1.5, "sight"),
    ("natural", "peak"): ("Gipfel", 0.6, "sight"),
    ("mountain_pass", "yes"): ("Pass", 2.5, "sight"),
    ("natural", "waterfall"): ("Wasserfall", 2.5, "sight"),
    ("natural", "beach"): ("Badestelle", 1.0, "sight"),
    ("amenity", "cafe"): ("Café", 0, "food"),
    ("amenity", "restaurant"): ("Restaurant", 0, "food"),
    ("amenity", "biergarten"): ("Biergarten", 0, "food"),
    ("amenity", "ice_cream"): ("Eiscafé", 0, "food"),
    ("amenity", "fuel"): ("Tankstelle", 0, "fuel"),
    # Wohnmobil/Gespann (RigAnalyzer)
    ("tourism", "caravan_site"): ("Wohnmobil-Stellplatz", 0, "camp"),
    ("tourism", "camp_site"): ("Campingplatz", 0, "camp"),
    ("amenity", "sanitary_dump_station"): ("Entsorgung", 0, "camp"),
    ("amenity", "water_point"): ("Frischwasser", 0, "camp"),
    ("amenity", "drinking_water"): ("Trinkwasser", 0, "camp"),
    ("highway", "services"): ("Raststätte", 0, "rest"),
    ("highway", "rest_area"): ("Rastplatz", 0, "rest"),
}


MAX_STAGES = 25


class AnalyzeError(ValueError):
    pass


# --------------------------------------------------------------------------- Geometrie

class Proj:
    """Lokale Abstandstreue Projektion (Meter) – reicht fuer Strecken bis ~500 km."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.cos(math.radians(lat0)) * 111320.0
        self.ky = 110574.0

    def fwd(self, lat, lon):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * self.ky

    def inv(self, x, y):
        return np.asarray(y) / self.ky + self.lat0, np.asarray(x) / self.kx + self.lon0


def _resample(xy, step):
    d = np.hypot(*np.diff(xy, axis=0).T)
    keep = np.concatenate([[True], d > 0.05])
    xy = xy[keep]
    d = np.hypot(*np.diff(xy, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(d)])
    n = max(int(round(s[-1] / step)) + 1, 2)
    ss = np.linspace(0.0, s[-1], n)
    return np.column_stack([np.interp(ss, s, xy[:, 0]), np.interp(ss, s, xy[:, 1])]), ss, s, keep


def _smooth(a, w):
    if w <= 1 or len(a) < w:
        return a
    k = np.ones(w) / w
    pad = w // 2
    ap = np.pad(a, (pad, w - 1 - pad), mode="edge")
    return np.convolve(ap, k, mode="valid")


def _runs(mask):
    """[(start, end_exclusive), ...] zusammenhaengender True-Bereiche."""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.diff(m.astype(int))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


# --------------------------------------------------------------------------- GPX

GPXX_RPT = "{http://www.garmin.com/xmlschemas/GpxExtensions/v3}rpt"


def _route_pts(r):
    """Routenpunkte inkl. Garmin-Zwischenpunkte (gpxx:rpt): Garmin/BaseCamp legen die
    berechnete Strecke dort ab - ohne sie bleiben nur die Wegpunkte im km-Abstand."""
    out = []
    for p in r.points:
        out.append((p.latitude, p.longitude, p.elevation))
        for ext in p.extensions or []:
            for q in ext.iter(GPXX_RPT):
                try:
                    out.append((float(q.get("lat")), float(q.get("lon")), None))
                except (TypeError, ValueError):
                    pass
    return [q for i, q in enumerate(out) if i == 0 or q[:2] != out[i - 1][:2]]


def parse_gpx(raw: bytes):
    head = raw[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in raw.lower():
        raise AnalyzeError("GPX mit DOCTYPE/ENTITY wird nicht angenommen.")
    try:
        g = gpxpy.parse(raw.decode("utf-8", errors="replace"))
    except Exception as e:  # gpxpy wirft diverse Typen
        raise AnalyzeError(f"Datei ist kein lesbares GPX ({e.__class__.__name__}).") from e
    pts, name = [], None
    for t in g.tracks:
        name = name or t.name
        for seg in t.segments:
            pts += [(p.latitude, p.longitude, p.elevation) for p in seg.points]
    if len(pts) < 2:
        for r in g.routes:
            name = name or r.name
            pts += _route_pts(r)
    if len(pts) < 2:
        raise AnalyzeError("Im GPX steckt weder ein Track noch eine Route.")
    if len(pts) > 200_000:
        raise AnalyzeError("Zu viele Punkte (max. 200.000).")
    return pts, (name or g.name or "").strip()


def parse_stages(raw: bytes):
    """Mehrere Tracks (oder Routen) im GPX = Reise mit Etappen. -> [(name, naechte|None, punkte)]
    Naechte am Etappenziel optional in <desc> oder <cmt>: "naechte=2" / "Nächte: 2"."""
    head = raw[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in raw.lower():
        raise AnalyzeError("GPX mit DOCTYPE/ENTITY wird nicht angenommen.")
    try:
        g = gpxpy.parse(raw.decode("utf-8", errors="replace"))
    except Exception as e:
        raise AnalyzeError(f"Datei ist kein lesbares GPX ({e.__class__.__name__}).") from e
    out = []
    items = [(t.name, t.description or t.comment, [p for sg in t.segments for p in sg.points]) for t in g.tracks]
    if len([i for i in items if len(i[2]) >= 2]) < 2:
        items = [(r.name, r.description or r.comment, _route_pts(r)) for r in g.routes]
    else:
        items = [(n, d, [(p.latitude, p.longitude, p.elevation) for p in pts]) for n, d, pts in items]
    for name, desc, pts in items:
        if len(pts) < 2:
            continue
        m = re.search(r"n(?:ae|ä)chte\s*[=:]\s*(\d+)", desc or "", re.I)
        out.append(((name or "").strip(), int(m.group(1)) if m else None, pts))
    return out, (g.name or "").strip()


def stage_gpx(pts, name=""):
    """Punkte -> minimales GPX (fuer die Einzelanalyse einer Etappe)."""
    rows = "".join(f'<trkpt lat="{la:.6f}" lon="{lo:.6f}">' + (f"<ele>{e:.1f}</ele>" if e is not None else "") + "</trkpt>"
                   for la, lo, e in pts)
    nm = (name or "").replace("&", "&amp;").replace("<", "&lt;")
    return (f'<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            f"<trk><name>{nm}</name><trkseg>{rows}</trkseg></trk></gpx>").encode()


# --------------------------------------------------------------------------- Overpass

def _poly_string(lat, lon, xy, proj, tol):
    line = LineString(xy).simplify(tol)
    sx, sy = np.asarray(line.coords).T
    la, lo = proj.inv(sx, sy)
    return ",".join(f"{a:.5f},{b:.5f}" for a, b in zip(la, lo))


def _q_roads(poly, radius=MATCH_RADIUS):
    return f"""[out:json][timeout:180][maxsize:536870912];
way["highway"]["highway"!~"^({ROAD_EXCLUDE})$"]["area"!="yes"](around:{radius:.0f},{poly})->.r;
.r out body geom qt;
(
  node(w.r)["highway"~"^(traffic_signals|stop|give_way|mini_roundabout)$"];
  node(w.r)["highway"="crossing"]["crossing"="traffic_signals"];
  node(w.r)["railway"="level_crossing"];
  node(w.r)["barrier"="height_restrictor"];
  node(w.r)["maxheight"];
  node(w.r)["maxweight"];
  node(w.r)["maxwidth"];
);
out body qt;"""


def _q_pois(poly):
    return f"""[out:json][timeout:180];
(
  nwr["tourism"~"^(viewpoint|attraction|museum|picnic_site|zoo)$"](around:800,{poly});
  nwr["historic"~"^(castle|ruins|monument|manor|fort|city_gate|monastery|archaeological_site|windmill|watermill)$"](around:800,{poly});
  nwr["man_made"="tower"]["tower:type"="observation"](around:800,{poly});
  nwr["man_made"~"^(lighthouse|windmill|watermill)$"](around:800,{poly});
  nwr["natural"~"^(peak|waterfall|beach)$"](around:800,{poly});
  node["mountain_pass"="yes"](around:300,{poly});
  nwr["amenity"~"^(cafe|restaurant|biergarten|ice_cream)$"](around:250,{poly});
  nwr["amenity"="fuel"](around:500,{poly});
  nwr["highway"~"^(services|rest_area)$"](around:500,{poly});
  nwr["tourism"~"^(caravan_site|camp_site)$"](around:3000,{poly});
  nwr["amenity"~"^(sanitary_dump_station|water_point|drinking_water)$"](around:3000,{poly});
  node["place"~"^(city|town|village|hamlet|suburb)$"](around:1500,{poly});
);
out center tags qt;"""


def _q_land(poly, bbox):
    s, w, n, e = bbox
    lu = "forest|farmland|meadow|grass|orchard|vineyard|residential|industrial|commercial|retail|farmyard|allotments|quarry|reservoir|basin"
    nat = "wood|water|scrub|heath|wetland|grassland|beach"
    return f"""[out:json][timeout:180][maxsize:536870912];
(
  way["landuse"~"^({lu})$"](around:80,{poly});
  way["natural"~"^({nat})$"](around:80,{poly});
  rel["landuse"~"^({lu})$"]["type"="multipolygon"](around:80,{poly});
  rel["natural"~"^({nat})$"]["type"="multipolygon"](around:80,{poly});
  way["waterway"~"^(river|canal)$"](around:300,{poly});
);
out geom({s:.4f},{w:.4f},{n:.4f},{e:.4f}) qt;"""


# --------------------------------------------------------------------------- Helfer OSM

def _width(tags):
    for k in ("width", "est_width"):
        m = re.match(r"\s*(\d+(?:[.,]\d+)?)", tags.get(k) or "")
        if m:
            return float(m.group(1).replace(",", "."))
    return None


def _maxspeed(tags, urban_default):
    v = (tags.get("maxspeed") or "").strip().lower()
    if not v:
        return None
    if v in ("none", "de:motorway"):
        return 130.0
    if v in ("walk", "de:walk", "de:living_street"):
        return 7.0
    if "urban" in v or v == "de:zone30" or "zone" in v:
        m = re.search(r"(\d+)", v)
        return float(m.group(1)) if m else 50.0
    if "rural" in v:
        return 100.0
    m = re.match(r"(\d+(?:\.\d+)?)\s*(mph)?", v)
    if not m:
        return None
    s = float(m.group(1))
    return s * 1.609 if m.group(2) else s


def _is_urban_way(tags):
    hw = tags.get("highway", "")
    if hw in ("residential", "living_street"):
        return True
    for k in ("maxspeed", "source:maxspeed", "zone:maxspeed", "zone:traffic", "maxspeed:type"):
        v = (tags.get(k) or "").lower()
        if "urban" in v or "zone30" in v or "zone:30" in v:
            return True
    ms = _maxspeed(tags, None)
    return ms is not None and ms <= 50 and hw not in ("track", "service")


def _parts(proj, geom):
    """OSM-Geometrie -> Liste zusammenhaengender xy-Arrays. Mit out geom(bbox)
    stehen fuer Punkte ausserhalb der Box None-Eintraege in der Liste."""
    out, cur = [], []
    for g in geom or []:
        if g is None:
            if len(cur) > 1:
                out.append(cur)
            cur = []
        else:
            cur.append((g["lat"], g["lon"]))
    if len(cur) > 1:
        out.append(cur)
    res = []
    for c in out:
        la, lo = zip(*c)
        x, y = proj.fwd(la, lo)
        res.append(np.column_stack([x, y]))
    return res


def _base_class(hw):
    return hw[:-5] if hw.endswith("_link") else (hw if hw in RANK else ("road" if hw else "?"))


# --------------------------------------------------------------------------- Rekonstruktion

def _dijkstra(adj, start, targets, limit):
    """Kuerzeste Wege von start zu den targets (bis limit Meter). -> {target: (dist, pfad)}"""
    dist, prev, heap, todo = {start: 0.0}, {}, [(0.0, start)], set(targets)
    while heap and todo:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, 1e18):
            continue
        if d > limit:
            break
        todo.discard(u)
        for v, w in adj[u]:
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v], prev[v] = nd, u
                heapq.heappush(heap, (nd, v))
    out = {}
    for t in targets:
        if t in dist and dist[t] <= limit:
            path = [t]
            while path[-1] != start:
                path.append(prev[path[-1]])
            out[t] = (dist[t], path[::-1])
    return out


GAP_M = 3000   # Aufzeichnungsluecke, ab der der ergaenzte Verlauf nur geraten ist
GAP_ROUTE_MAX = 40000   # groessere Luecken (Faehre, Tunnel) bleiben „unbekannt“
# Fuer grosse Luecken nur Durchgangsstrassen im breiten Korridor – sonst ertrinkt der Pi in Wohnstrassen
GAP_CLASSES = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
               "secondary_link", "tertiary", "tertiary_link", "unclassified"}


def _recon_factor(tg):
    """Gewicht je Strasse fuer die Rekonstruktion duenner Aufzeichnungen: wie ein Fahrer –
    Haupt-/Landstrassen guenstig, Wirtschaftswege/Zufahrten teuer, Privates/Gesperrtes sehr teuer."""
    hw = tg.get("highway", "")
    f = {"motorway": 0.75, "motorway_link": 0.9, "trunk": 0.8, "trunk_link": 0.9, "primary": 0.85,
         "primary_link": 0.95, "secondary": 0.9, "secondary_link": 1.0, "tertiary": 1.0,
         "unclassified": 1.3, "residential": 1.4, "living_street": 2.0, "service": 3.0,
         "track": 6.0}.get(hw, 1.5)
    acc = tg.get("motor_vehicle") or tg.get("vehicle") or tg.get("access") or ""
    if acc in ("private", "no", "agricultural", "forestry", "permit"):
        f *= 10
    elif acc in ("destination", "delivery", "customers"):
        f *= 1.5
    return f


def _reconstruct(pts, progress):
    """Ausgeduennte Planer-Exporte (z. B. Calimoto: ein Punkt alle ~250 m) entlang
    des echten Strassennetzes verdichten: jeder Punkt rastet auf das naechste
    Strassensegment ein, dazwischen kuerzester Weg im Netz. Ohne das waeren
    Kurven zwischen zwei Punkten unsichtbar (gerade Linie)."""
    lat = np.array([p[0] for p in pts])
    lon = np.array([p[1] for p in pts])
    proj = Proj(float(lat.mean()), float(lon.mean()))
    x, y = proj.fwd(lat, lon)
    xy = np.column_stack([x, y])
    d = np.hypot(*np.diff(xy, axis=0).T)
    d = d[d > 0.5]
    if len(d) == 0 or np.median(d) <= SPARSE_M:
        return pts, None
    progress(3, f"Route hat nur alle ~{np.median(d):.0f} m einen Punkt – Straßenverlauf wird rekonstruiert")

    Pc, _, _, _ = _resample(xy, 50.0)
    clat, clon = proj.inv(Pc[:, 0], Pc[:, 1])
    db = osmdb.get()
    ways = {}
    step = int(CHUNK_M / 50.0)
    cuts = list(range(0, len(Pc) - 1, step)) + [len(Pc) - 1]
    for a, b in zip(cuts[:-1], cuts[1:]):
        a0, b0 = max(a - 2, 0), min(b + 2, len(Pc) - 1)
        la, lo = clat[a0:b0 + 1], clon[a0:b0 + 1]
        if _local(db, la, lo):
            els = db.fetch(la, lo, proj, RECON_RADIUS)[0]
        else:
            poly = _poly_string(la, lo, Pc[a0:b0 + 1], proj, 30.0)
            els = overpass.query(_q_roads(poly, RECON_RADIUS), require=True)["elements"]
        for el in els:
            if el["type"] == "way" and "geometry" in el:
                ways[el["id"]] = el
    # Grosse Luecken (3–40 km, z. B. ein Punkt alle 5 km): die echte Strasse liegt oft weit neben der Luftlinie –
    # breiter Korridor nur mit Durchgangsstrassen, rund um die beiden Punkte alle Strassen (Anschluss)
    if db is not None:
        for i in range(len(xy) - 1):
            chord = math.hypot(*(xy[i + 1] - xy[i]))
            if not GAP_M < chord <= GAP_ROUTE_MAX:
                continue
            n = max(2, int(chord / 250) + 1)
            gx = np.linspace(xy[i][0], xy[i + 1][0], n)
            gy = np.linspace(xy[i][1], xy[i + 1][1], n)
            gla, glo = proj.inv(gx, gy)
            if not _local(db, gla, glo):
                continue
            r = min(max(0.5 * chord, 2000.0), 6000.0)
            for el in db.roads_near(gla, glo, proj, r, GAP_CLASSES) + \
                    db.roads_near(gla[[0, -1]], glo[[0, -1]], proj, 600.0):
                ways[el["id"]] = el
    if not ways:
        return pts, None

    nxy, adj = {}, defaultdict(list)
    seg_a, seg_b, seg_u, seg_v, seg_f = [], [], [], [], []
    for w in ways.values():
        ids, g = w["nodes"], w["geometry"]
        f = _recon_factor(w.get("tags", {}))
        gx, gy = proj.fwd([q["lat"] for q in g], [q["lon"] for q in g])
        for k, nid in enumerate(ids):
            nxy[nid] = (float(gx[k]), float(gy[k]))
        for k in range(len(ids) - 1):
            wl = math.hypot(gx[k + 1] - gx[k], gy[k + 1] - gy[k]) * f
            adj[ids[k]].append((ids[k + 1], wl))
            adj[ids[k + 1]].append((ids[k], wl))
            seg_a.append((gx[k], gy[k]))
            seg_b.append((gx[k + 1], gy[k + 1]))
            seg_u.append(ids[k])
            seg_v.append(ids[k + 1])
            seg_f.append(f)
    A, B = np.array(seg_a), np.array(seg_b)
    tree = STRtree([LineString([a, b]) for a, b in zip(A, B)])

    snaps = []   # je Punkt: (segment, Projektionspunkt) oder None
    for p in xy:
        # Kandidaten im 60-m-Umkreis; oeffentliche Strasse schlaegt private Zufahrt daneben
        cand = tree.query(Point(p).buffer(60))
        best_s = None
        for j in (cand if len(cand) else [tree.nearest(Point(p))]):
            ab = B[j] - A[j]
            t = float(np.clip(np.dot(p - A[j], ab) / max(np.dot(ab, ab), 1e-9), 0, 1))
            q = A[j] + ab * t
            dist = math.hypot(*(p - q))
            score = dist + 15 * (seg_f[j] - 1)
            if dist <= 60 and (best_s is None or score < best_s[0]):
                best_s = (score, int(j), q)
        snaps.append(best_s[1:] if best_s else None)

    out = [xy[0] if snaps[0] is None else snaps[0][1]]
    n_routed = 0
    n_gap_routed = 0
    gaps = []   # Luecken > GAP_M, die NICHT ueber das Strassennetz geschlossen werden konnten: Verlauf unbekannt
    for i in range(len(xy) - 1):
        is_gap = math.hypot(*(xy[i + 1] - xy[i])) > GAP_M
        s0, s1 = snaps[i], snaps[i + 1]
        if is_gap and math.hypot(*(xy[i + 1] - xy[i])) > GAP_ROUTE_MAX:
            gaps.append((xy[i], xy[i + 1]))
            out.append(xy[i + 1] if s1 is None else s1[1])
            continue
        if s0 is None or s1 is None:
            if is_gap:
                gaps.append((xy[i], xy[i + 1]))
            out.append(xy[i + 1] if s1 is None else s1[1])
            continue
        (j0, q0), (j1, q1) = s0, s1
        if j0 == j1:
            out.append(q1)
            continue
        chord = math.hypot(*(q1 - q0))
        limit = 3.5 * chord + 500   # gewichtete Meter (Nebenstrassen zaehlen mehr)
        ends = {seg_u[j1]: math.hypot(*(np.array(nxy[seg_u[j1]]) - q1)),
                seg_v[j1]: math.hypot(*(np.array(nxy[seg_v[j1]]) - q1))}
        best = None
        for st in (seg_u[j0], seg_v[j0]):
            c0 = math.hypot(*(np.array(nxy[st]) - q0))
            for t, (dd, path) in _dijkstra(adj, st, list(ends), limit).items():
                c = c0 + dd + ends[t]
                if best is None or c < best[0]:
                    best = (c, path)
        if best is None or best[0] > limit:
            if is_gap:
                gaps.append((xy[i], xy[i + 1]))
            out.append(q1)
            continue
        out += [np.array(nxy[n]) for n in best[1]]
        out.append(q1)
        n_routed += 1
        n_gap_routed += is_gap
    out = np.array(out)
    la, lo = proj.inv(out[:, 0], out[:, 1])
    gl = []
    for a, b in gaps:
        gla, glo = proj.inv(np.array([a[0], b[0]]), np.array([a[1], b[1]]))
        gl.append([[float(gla[0]), float(glo[0])], [float(gla[1]), float(glo[1])]])
    info = {"points": len(pts), "median_m": round(float(np.median(d))), "routed": n_routed, "gaps": gl,
            "gaps_routed": n_gap_routed}
    return [(float(a), float(b), None) for a, b in zip(la, lo)], info


# --------------------------------------------------------------------------- Hauptfunktion

def _local(db, lat, lon):
    """Reichen die lokalen Kartendaten fuer diese Punkte? Punkte ausserhalb der Abdeckung sind
    unkritisch, wenn sie in keiner Region liegen (Meer: Faehre, Kanal-Ueberfahrt) oder nur im
    Randstreifen einer Region, die lokal vorliegt. Nur echte Luecken brauchen Overpass."""
    if db is None:
        return False
    lat, lon = np.asarray(lat), np.asarray(lon)
    inside = shapely.contains_xy(db.inner, lon, lat)
    if inside.all():
        return True
    have = db.regions

    def ok(r):   # vorhanden, Teil einer vorhandenen Region oder Sammelregion aus vorhandenen Teilen
        if r is None:
            return False
        # Geofabrik fuehrt England/Schottland/Wales doppelt: united-kingdom/* = great-britain/*
        alias = [r]
        if r.startswith("europe/united-kingdom"):
            alias.append("europe/ireland-and-northern-ireland" if "northern-ireland" in r
                         else r.replace("europe/united-kingdom", "europe/great-britain"))
        return any(a == h or a.startswith(h + "/") or h.startswith(a + "/") for a in alias for h in have)
    return all(ok(m["region"]) for m in regions.missing_for(lat, lon, inside))


def needs_live(raw: bytes, pts=None) -> bool:
    """Schnelle Vorpruefung: liegt die Route (teilweise) ausserhalb der lokalen DB?
    Solche Analysen brauchen Overpass und laufen in einer eigenen Warteschlange.
    pts: schon gelesene Punkte (0.46.0, GPX nur einmal lesen)."""
    if pts is None:
        try:
            pts, _ = parse_gpx(raw)
        except AnalyzeError:
            return False
    db = osmdb.get()
    if db is None:
        return True
    la = np.array([p[0] for p in pts])
    lo = np.array([p[1] for p in pts])
    sel = np.linspace(0, len(la) - 1, min(len(la), 500)).astype(int)
    return not _local(db, la[sel], lo[sel])


def _region_of(lat, lon):
    """Geofabrik-Region je Punkt: lokale Regions-DB, sonst kleinste Katalog-Region."""
    lat, lon = np.asarray(lat), np.asarray(lon)
    out = [None] * len(lat)
    db = osmdb.get()
    if db is not None:
        for d in db.dbs:
            if len(d.regions) != 1:
                continue
            inside = shapely.contains_xy(d.coverage, lon, lat)
            for k in np.where(inside)[0]:
                if out[k] is None:
                    out[k] = d.regions[0]
    rest = [k for k, v in enumerate(out) if v is None]
    if rest:
        idx = regions._load_index() or []
        for k in rest:
            for r in idx:
                if r["area"] > 1e-6 and shapely.contains_xy(r["geom"], float(lon[k]), float(lat[k])):
                    out[k] = r["region"]
                    break
    return out


def analyze(raw: bytes, filename: str = "", progress=lambda p, m: None, info=None, options=None) -> dict:
    """info (dict, optional) wird schon waehrend der Analyse befuellt – u. a. mit
    "missing" (Regionen ohne lokale Daten), damit die Oberflaeche das auch bei einem
    Overpass-Fehler anzeigen kann."""
    info = info if info is not None else {}
    options = options or {}
    progress(2, "GPX wird gelesen")
    pts, name = parse_gpx(raw)
    # Welche Teile liegen ausserhalb der lokalen DB? -> Region zum Nachbestellen.
    # Vor jedem Overpass-Zugriff, damit die Info auch bei einem Fehler vorliegt.
    try:
        la0 = np.array([p[0] for p in pts])
        lo0 = np.array([p[1] for p in pts])
        sel = np.linspace(0, len(la0) - 1, min(len(la0), 1500)).astype(int)
        db0 = osmdb.get()
        cov = (shapely.contains_xy(db0.inner, lo0[sel], la0[sel]) if db0 is not None
               else np.zeros(len(sel), bool))
        info["missing"] = regions.missing_for(la0[sel], lo0[sel], cov)
    except Exception as e:   # noqa: BLE001 – Katalog nicht erreichbar o. ae. – Analyse trotzdem versuchen
        info["missing"] = []
        logging.getLogger("analyze").warning("Regionspruefung fehlgeschlagen: %s", e)
    if info["missing"]:
        progress(3, "Teile der Route liegen außerhalb der lokalen Kartendaten – hole Live-Daten (kann dauern)")
    pts, recon = _reconstruct(pts, progress)
    lat = np.array([p[0] for p in pts])
    lon = np.array([p[1] for p in pts])
    ele_raw = np.array([p[2] if p[2] is not None else np.nan for p in pts], float)
    proj = Proj(float(lat.mean()), float(lon.mean()))
    xr, yr = proj.fwd(lat, lon)
    xy_raw = np.column_stack([xr, yr])
    P, S, s_raw, keep = _resample(xy_raw, STEP)
    total = float(S[-1])
    if total < 500:
        raise AnalyzeError("Die Strecke ist kürzer als 500 m.")
    if total > MAX_KM * 1000:
        raise AnalyzeError(f"Die Strecke ist {total/1000:.0f} km lang – RideAnalyzer nimmt bis {MAX_KM:.0f} km.")
    # Positionen leicht glaetten (GPS-Rauschen bei aufgezeichneten Tracks)
    P = np.column_stack([_smooth(P[:, 0], 3), _smooth(P[:, 1], 3)])
    N = len(P)
    plat, plon = proj.inv(P[:, 0], P[:, 1])
    ele = None
    e_k = ele_raw[keep]
    if np.isfinite(e_k).sum() > len(e_k) * 0.8:
        ok = np.isfinite(e_k)
        ele = _smooth(np.interp(S, s_raw[ok], e_k[ok]), 5)

    unsure = []
    for (a, b) in (recon or {}).get("gaps", []):
        ia = int(np.argmin(np.hypot(*(P - np.array(proj.fwd(a[0], a[1]))).T)))
        ib = int(np.argmin(np.hypot(*(P - np.array(proj.fwd(b[0], b[1]))).T)))
        if ib > ia:
            unsure.append((S[ia] / 1000, S[ib] / 1000))

    dxy = np.gradient(P, axis=0)
    heading = np.arctan2(dxy[:, 1], dxy[:, 0])

    # ------------------------------------------------ OSM-Daten (in Teilstuecken)
    # Je ~20-km-Stueck: lokale DB, wenn das Stueck in ihrem Gebiet liegt, sonst
    # Overpass. Kleine Overpass-Abfragen kommen bei Last eher durch und landen
    # einzeln im Cache (Wiederholung setzt dort an).
    warnings = []
    if recon:
        warnings.append(f"Ausgedünnte Route ({recon['points']} Punkte, im Schnitt alle {recon['median_m']} m): "
                        f"Straßenverlauf zwischen den Punkten aus dem Straßennetz ergänzt "
                        f"({recon['routed']} Lücken geschlossen).")
    db = osmdb.get()
    chunk = int(CHUNK_M / STEP)
    cuts = list(range(0, N - 1, chunk)) + [N - 1]
    if len(cuts) > 2 and cuts[-1] - cuts[-2] < chunk // 4:
        cuts.pop(-2)
    pieces = list(zip(cuts[:-1], cuts[1:]))
    roads_el, pois_el, land_el = {}, {}, []
    polys, pcls, rivers = [], [], []
    land_missing = n_local = 0

    def _to_xy(c):
        x, y = proj.fwd(c[:, 1], c[:, 0])
        return np.column_stack([x, y])

    for n, (a, b) in enumerate(pieces):
        a0, b0 = max(a - 5, 0), min(b + 5, N - 1)
        base = 5 + 50 * n / len(pieces)
        step_pct = 50 / len(pieces)
        part = f"Teilstück {n + 1}/{len(pieces)} (km {S[a]/1000:.0f}–{S[b]/1000:.0f})"
        if _local(db, plat[a0:b0 + 1], plon[a0:b0 + 1]):
            progress(base, f"{part}: lokale Kartendaten")
            r_el, p_el, l_items, rv = db.fetch(plat[a0:b0 + 1], plon[a0:b0 + 1], proj, MATCH_RADIUS)
            for el in r_el:
                roads_el[(el["type"], el["id"])] = el
            for el in p_el:
                pois_el[(el["type"], el["id"])] = el
            for g, kv in l_items:
                c = LAND_CLASS.get(kv.split("=", 1)[1])
                if c:
                    pg = shapely.transform(g, _to_xy)
                    polys.append(pg if pg.is_valid else pg.buffer(0))
                    pcls.append(c)
            rivers += [LineString(_to_xy(c[:, ::-1])) for c in rv if len(c) > 1]
            n_local += 1
            continue
        poly = _poly_string(plat[a0:b0 + 1], plon[a0:b0 + 1], P[a0:b0 + 1], proj, 30.0)
        bb = (float(plat[a0:b0 + 1].min()) - 0.01, float(plon[a0:b0 + 1].min()) - 0.015,
              float(plat[a0:b0 + 1].max()) + 0.01, float(plon[a0:b0 + 1].max()) + 0.015)

        def note(m, _p=base, _part=part):
            progress(_p, f"{_part}: {m}")

        progress(base, f"{part}: Straßennetz (Overpass)")
        for el in overpass.query(_q_roads(poly), require=True, notify=note)["elements"]:
            roads_el[(el["type"], el["id"])] = el
        progress(base + step_pct / 3, f"{part}: Sehenswertes, Einkehr, Orte (Overpass)")
        for el in overpass.query(_q_pois(poly), notify=note)["elements"]:
            pois_el[(el["type"], el["id"])] = el
        progress(base + 2 * step_pct / 3, f"{part}: Landschaft (Overpass)")
        try:
            land_el += overpass.query(_q_land(poly, bb), notify=note)["elements"]
        except overpass.OverpassError:
            land_missing += 1
    if land_missing:
        warnings.append(f"Landschaftsdaten fehlen für {land_missing} von {len(pieces)} Teilstücken (Kartenserver ausgelastet).")
    source = ("lokal" if n_local == len(pieces) else
              "Overpass" if n_local == 0 else f"gemischt ({n_local}/{len(pieces)} lokal)")
    roads = {"elements": list(roads_el.values())}
    pois_raw = {"elements": list(pois_el.values())}
    land_raw = {"elements": land_el}

    progress(60, "Strecke wird dem Straßennetz zugeordnet")
    ways, nodes = [], {}
    for el in roads.get("elements", []):
        if el["type"] == "way" and "geometry" in el:
            ways.append(el)
        elif el["type"] == "node":
            nodes[el["id"]] = el
    if not ways:
        raise AnalyzeError("Entlang der Strecke wurden keine Straßen gefunden.")

    degree = Counter()
    node_xy = {}
    node_ways = defaultdict(set)
    for wi, w in enumerate(ways):
        ids = w["nodes"]
        gx, gy = proj.fwd([g["lat"] for g in w["geometry"]], [g["lon"] for g in w["geometry"]])
        w["_xy"] = np.column_stack([gx, gy])
        tg = w.get("tags", {})
        w["_cls"] = _base_class(tg.get("highway", ""))
        w["_rank"] = RANK.get(w["_cls"], 1)
        w["_urban"] = _is_urban_way(tg)
        w["_round"] = tg.get("junction") in ("roundabout", "circular")
        for k, nid in enumerate(ids):
            degree[nid] += 1 if k in (0, len(ids) - 1) else 2
            node_xy[nid] = (gx[k], gy[k])
            node_ways[nid].add(wi)

    sa, sb, sw = [], [], []
    for wi, w in enumerate(ways):
        xy = w["_xy"]
        sa.append(xy[:-1])
        sb.append(xy[1:])
        sw.append(np.full(len(xy) - 1, wi))
    A = np.concatenate(sa)
    B = np.concatenate(sb)
    W = np.concatenate(sw)
    seg_geoms = [LineString([a, b]) for a, b in zip(A, B)]
    tree = STRtree(seg_geoms)
    pgeom = shp_points(P)
    pi, si = tree.query(pgeom, predicate="dwithin", distance=MATCH_RADIUS)

    AB = B - A
    L2 = np.maximum((AB ** 2).sum(1), 1e-9)
    t = np.clip(((P[pi] - A[si]) * AB[si]).sum(1) / L2[si], 0, 1)
    proj_pt = A[si] + AB[si] * t[:, None]
    dist = np.hypot(*(P[pi] - proj_pt).T)
    seg_h = np.arctan2(AB[si, 1], AB[si, 0])
    dh = np.abs((heading[pi] - seg_h + np.pi / 2) % np.pi - np.pi / 2)  # 0..pi/2, richtungslos
    # Aufschlag fuer Wege, auf denen man mit Fahrzeug kaum faehrt: eine oeffentliche Strasse in
    # 15 m schlaegt die private Zufahrt/den Betriebsweg in 5 m daneben (sonst Schein-Verbote)
    pen = np.zeros(len(ways))
    for wi, w in enumerate(ways):
        tg = w.get("tags", {})
        acc = tg.get("motor_vehicle") or tg.get("motorcar") or tg.get("vehicle") or tg.get("access") or ""
        if acc in ("private", "no", "agricultural", "forestry", "permit"):
            pen[wi] += 20.0
        if tg.get("highway") in ("service", "track", "path", "living_street"):
            pen[wi] += 8.0
    cost = dist + np.degrees(dh) * 0.35 + pen[W[si]]

    order = np.lexsort((cost, pi))
    pi_s, si_s, cost_s = pi[order], si[order], cost[order]
    starts = np.searchsorted(pi_s, np.arange(N))
    ends = np.searchsorted(pi_s, np.arange(N), side="right")
    match = np.full(N, -1)
    prev = -1
    for i in range(N):
        a, b = starts[i], ends[i]
        if a == b:
            prev = -1
            continue
        best_w, best_c = W[si_s[a]], cost_s[a]
        if prev >= 0 and best_w != prev:
            for k in range(a, b):
                if W[si_s[k]] == prev and cost_s[k] - 6.0 <= best_c:
                    best_w = prev
                    break
        match[i] = best_w
        prev = best_w
    matched_share = float((match >= 0).mean())
    if matched_share < 0.5:
        warnings.append(f"Nur {matched_share*100:.0f} % der Strecke liegen auf bekannten Straßen.")

    cls = np.array([ways[m]["_cls"] if m >= 0 else "?" for m in match], dtype=object)
    rank = np.array([ways[m]["_rank"] if m >= 0 else 1 for m in match])
    urban_way = np.array([ways[m]["_urban"] if m >= 0 else False for m in match])
    in_round = np.array([ways[m]["_round"] if m >= 0 else False for m in match])
    surf_score = np.full(N, np.nan)
    surf_lbl = np.empty(N, dtype=object)
    vlimit = np.full(N, np.nan)
    narrow = np.zeros(N, bool)
    road_key = np.empty(N, dtype=object)   # Strassen-Identitaet (ref/Name) fuer Schlenker-Erkennung
    for m in np.unique(match[match >= 0]):
        tg = ways[m].get("tags", {})
        sel = match == m
        wd = _width(tg)
        # einspurig/schmal nur bei Gegenverkehr – Kreisel, Auffahrten, Einbahnstrassen haben eine Spur
        one = (tg.get("oneway") in ("yes", "-1", "1", "true") or tg.get("junction") in ("roundabout", "circular")
               or tg.get("highway") in ("motorway", "motorway_link"))
        if (wd is not None and wd < (3.0 if one else 4.5)) or (tg.get("lanes") == "1" and not one):
            narrow[sel] = True
        road_key[sel] = tg.get("ref") or tg.get("name")
        sfc = tg.get("surface")
        sc = SURFACE_SCORE.get(sfc) if sfc else None
        if sc is not None and tg.get("smoothness") in SMOOTHNESS_FACTOR:
            sc *= SMOOTHNESS_FACTOR[tg["smoothness"]]
        ms = _maxspeed(tg, None)
        if sc is not None:
            surf_score[sel] = sc
        surf_lbl[sel] = SURFACE_LABEL.get(sfc, sfc) if sfc else None
        if ms is not None:
            vlimit[sel] = ms

    # ------------------------------------------------ Landschaft
    progress(70, "Landschaft wird ausgewertet")
    for el in land_raw.get("elements", []):
        tg = el.get("tags", {})
        if tg.get("waterway") in ("river", "canal") and el["type"] == "way":
            rivers += [LineString(xy) for xy in _parts(proj, el.get("geometry"))]
            continue
        c = LAND_CLASS.get(tg.get("landuse")) or LAND_CLASS.get(tg.get("natural"))
        if tg.get("water") in ("river", "lake", "pond", "reservoir"):
            c = "wasser"
        if not c:
            continue
        try:
            if el["type"] == "way":
                xy = np.concatenate(_parts(proj, el.get("geometry")) or [np.zeros((0, 2))])
                if len(xy) >= 4:
                    pg = Polygon(xy).buffer(0)
                    if not pg.is_empty:
                        polys.append(pg)
                        pcls.append(c)
            elif el["type"] == "relation":
                outer, inner = [], []
                for mb in el.get("members", []):
                    if mb.get("type") != "way":
                        continue
                    for xy in _parts(proj, mb.get("geometry")):
                        (inner if mb.get("role") == "inner" else outer).append(LineString(xy))
                if outer:
                    pg = unary_union(list(polygonize(linemerge(outer))))
                    if inner:
                        hole = unary_union(list(polygonize(linemerge(inner))))
                        pg = pg.difference(hole)
                    if not pg.is_empty:
                        polys.append(pg.buffer(0))
                        pcls.append(c)
        except Exception:  # noqa: BLE001 – kaputte Geometrie ueberspringen, Analyse laeuft weiter
            continue
    land = np.empty(N, dtype=object)
    near_river = np.zeros(N, bool)
    idx = np.arange(0, N, 10)  # alle 100 m
    if polys:
        ltree = STRtree(polys)
        nx_, ny_ = -np.sin(heading[idx]), np.cos(heading[idx])
        votes = [Counter() for _ in idx]
        for side in (-60.0, 60.0):
            q = shp_points(np.column_stack([P[idx, 0] + nx_ * side, P[idx, 1] + ny_ * side]))
            qi, gi = ltree.query(q, predicate="within")
            best = {}
            for a, g in zip(qi, gi):
                c = pcls[g]
                if a not in best or LAND_PRIO[c] > LAND_PRIO[best[a]]:
                    best[a] = c
            for a, c in best.items():
                votes[a][c] += 1
        for k, i in enumerate(idx):
            if votes[k]:
                c = max(votes[k].items(), key=lambda kv: (kv[1], LAND_PRIO[kv[0]]))[0]
                land[i:i + 10] = c
    if rivers:
        rtree = STRtree(rivers)
        qi, _ = rtree.query(shp_points(P[idx]), predicate="dwithin", distance=250)
        for a in np.unique(qi):
            near_river[idx[a]:idx[a] + 10] = True

    # ------------------------------------------------ Ortsdurchfahrten
    land_settle = np.array([c in ("siedlung", "gewerbe") for c in land])
    urban = urban_way | (land_settle & (rank <= 5))
    urban = _clean_mask(urban, min_run=15, max_gap=15)  # < 150 m wegfiltern / Luecken fuellen

    places = []
    for el in pois_raw.get("elements", []):
        tg = el.get("tags", {})
        if "place" in tg and tg.get("name"):
            la, lo = (el.get("lat"), el.get("lon")) if "lat" in el else (el["center"]["lat"], el["center"]["lon"])
            px, py = proj.fwd(la, lo)
            places.append((float(px), float(py), tg["name"], tg["place"]))
    towns = []
    for a, b in _runs(urban):
        if (b - a) * STEP < 200:
            continue
        mid = P[(a + b) // 2]
        nm = None
        if places:
            d = [math.hypot(px - mid[0], py - mid[1]) for px, py, _, _ in places]
            k = int(np.argmin(d))
            if d[k] < 2500:
                nm = places[k][2]
        towns.append({"name": nm or "Ortslage", "km0": round(float(S[a]) / 1000, 2), "km1": round(float(S[b - 1]) / 1000, 2),
                      "lat": float(plat[(a + b) // 2]), "lon": float(plon[(a + b) // 2])})
    merged = []
    for t_ in towns:
        if merged and merged[-1]["name"] == t_["name"] and t_["km0"] - merged[-1]["km1"] < 1.0:
            merged[-1]["km1"] = t_["km1"]
        else:
            merged.append(t_)
    towns = merged

    # ------------------------------------------------ Ereignisse (Fahrfluss)
    progress(78, "Fahrfluss wird bewertet")
    events = []
    ptree = STRtree(pgeom)

    # benannte Orte an der Strecke (Stadt/Kleinstadt/Dorf bis 2 km daneben) – Namen und echte Streckenpunkte
    # fuer die Zwischenpunkte, wenn eine importierte Tour neu geplant wird (static-rig/importplan.js)
    near_places = {}
    cand = [(x_, y_, nm_, PLACE_RANK[pl_]) for x_, y_, nm_, pl_ in places if pl_ in PLACE_RANK]
    if cand:
        qi, ti = ptree.query_nearest(shp_points(np.array([(c[0], c[1]) for c in cand])), max_distance=PLACE_NEAR_M,
                                     all_matches=False)
        for a, j in zip(qi, ti):
            x_, y_, nm_, r_ = cand[int(a)]
            d_ = math.hypot(P[j, 0] - x_, P[j, 1] - y_)
            old = near_places.get(nm_)
            if old is None or (r_, d_) < (old["r"], old["d"]):
                near_places[nm_] = {"name": nm_, "r": r_, "d": d_, "i": int(j)}
    near_places = sorted(near_places.values(), key=lambda q: q["i"])[:PLACE_MAX]

    def _nearest_idx(x, y, maxd):
        g = Point(x, y)
        j = ptree.nearest(g)
        if j is None:
            return None
        return int(j) if math.hypot(P[j, 0] - x, P[j, 1] - y) <= maxd else None

    route_ways = set(int(m) for m in np.unique(match[match >= 0]))
    for nid, el in nodes.items():
        if nid not in node_xy or not (node_ways[nid] & route_ways):
            continue
        tg = el.get("tags", {})
        hw = tg.get("highway")
        if hw == "traffic_signals" or tg.get("crossing") == "traffic_signals":
            typ = "signal"
        elif hw in ("stop", "give_way"):
            typ = hw
        elif hw == "mini_roundabout":
            typ = "roundabout"
        elif tg.get("railway") == "level_crossing":
            typ = "level_crossing"
        else:
            continue
        x, y = node_xy[nid]
        i = _nearest_idx(x, y, 12.0)
        if i is None:
            continue
        # Knoten muss auf einem Weg liegen, den die Route hier tatsaechlich faehrt
        # (Ampel/Schild der Querstrasse zaehlt nicht)
        here = set(int(m) for m in match[max(i - 2, 0):i + 3] if m >= 0)
        if not (node_ways[nid] & here):
            continue
        # Richtungsabhaengige Schilder/Ampeln: nur zaehlen, wenn wir in Gueltigkeitsrichtung fahren
        dirv = tg.get("direction") or tg.get("traffic_signals:direction") or tg.get(f"{hw}:direction")
        if dirv in ("forward", "backward"):
            w = ways[next(iter(node_ways[nid] & here))]
            if nid in w["nodes"]:
                k = w["nodes"].index(nid)
                xy = w["_xy"]
                v = xy[min(k + 1, len(xy) - 1)] - xy[max(k - 1, 0)]
                fwd = (v[0] * math.cos(heading[i]) + v[1] * math.sin(heading[i])) > 0
                if fwd != (dirv == "forward"):
                    continue
        events.append([i, typ])

    # Kreisverkehre (Ways mit junction=roundabout): je Einfahrt ein Ereignis
    for a, _b in _runs(in_round):
        events.append([a, "roundabout"])

    junction_idx = []
    for nid, deg in degree.items():
        if deg < 3 or nid not in node_xy or not (node_ways[nid] & route_ways):
            continue
        x, y = node_xy[nid]
        i = _nearest_idx(x, y, 10.0)
        if i is None:
            continue
        junction_idx.append(i)
        a, b = max(i - 4, 0), min(i + 4, N - 1)
        wa, wb = match[a], match[b]
        if wa < 0 or wb < 0:
            continue
        turn = abs((heading[b] - heading[a] + np.pi) % (2 * np.pi) - np.pi)
        r_here = max(ways[wa]["_rank"], ways[wb]["_rank"])
        others = [ways[k]["_rank"] for k in node_ways[nid] if k not in (wa, wb)]
        prio = any(ways[k].get("tags", {}).get("priority_road") for k in (wa, wb))
        if in_round[a] or in_round[b]:
            continue
        if others and max(others) > r_here and not prio:
            events.append([i, "yield_implicit"])
        elif wa != wb and ways[wb]["_rank"] > ways[wa]["_rank"] and np.degrees(turn) > 30 and not prio:
            events.append([i, "yield_implicit"])
        elif np.degrees(turn) > 50:
            events.append([i, "turn"])
    junction_idx = np.array(sorted(junction_idx)) if junction_idx else np.array([], int)

    # Dedupe: Ereignisse innerhalb 40 m zu einem zusammenfassen (staerkstes gewinnt)
    events.sort(key=lambda e: e[0])
    ded = []
    for i, typ in events:
        if ded and i - ded[-1][0] <= 4:
            if EVENT[typ][1] > EVENT[ded[-1][1]][1]:
                ded[-1] = [ded[-1][0], typ]
            continue
        ded.append([i, typ])
    # implizite Wartepflicht direkt neben explizitem Schild ist doppelt
    events = ded
    ev_w = np.zeros(N)
    ev_t = np.zeros(N)
    for i, typ in events:
        ev_w[i] += EVENT[typ][1]
        ev_t[i] += EVENT[typ][2]

    # ------------------------------------------------ Kurven
    progress(85, "Kurven werden erkannt")
    seg_head = np.unwrap(np.arctan2(np.diff(P[:, 1]), np.diff(P[:, 0])))
    seg_head = _smooth(seg_head, 3)
    k = 2
    kappa = np.zeros(N)
    kappa[k:N - k] = (seg_head[2 * k - 1:] - seg_head[:N - 2 * k]) / (2 * k * STEP) if N > 2 * k + 1 else 0
    kappa = _smooth(kappa, 3)
    curves = []
    rough_notes_buf = []
    near_junction = np.zeros(N, bool)
    for j in junction_idx:
        near_junction[max(j - 3, 0):j + 4] = True
    round_zone = _clean_mask(in_round, 1, 0)
    for j in np.where(round_zone)[0]:
        round_zone[max(j - 3, 0):j + 4] = True
    for sign in (1, -1):
        mask = (kappa * sign) > (1 / 400.0)
        mask = _clean_mask(mask, 1, 2)
        for a, b in _runs(mask):
            ang = float(np.degrees(np.sum(kappa[a:b]) * STEP)) * sign
            if ang < 20:
                continue
            seg = np.abs(kappa[a:b])
            apex = a + int(np.argmax(seg))
            rmin = 1.0 / max(seg.max(), 1e-6)
            length = (b - a) * STEP
            if round_zone[apex]:
                continue
            if near_junction[apex] and length <= 70 and match[max(a - 3, 0)] != match[min(b + 3, N - 1)]:
                continue  # Abbiegevorgang an Kreuzung, keine Kurve
            if rmin < 30 and ang >= 120:
                c = "kehre"
            else:
                c = "eng" if rmin < 60 else ("mittel" if rmin < 150 else "weit")
            sc_ap = surf_score[a:b]
            rough = bool((np.isfinite(sc_ap) & (sc_ap < 50)).any() or narrow[apex])
            curves.append({"i": apex, "a": a, "b": b, "r": round(rmin), "angle": round(ang),
                           "cls": c, "sign": sign, "urban": bool(urban[apex]), "rough": rough,
                           "v": _curve_speed(rmin, rough, vlimit[apex], cls[apex])})
    curves.sort(key=lambda c: c["i"])
    for c1, c2 in zip(curves, curves[1:]):
        if c1["sign"] != c2["sign"] and (c2["a"] - c1["b"]) * STEP < 60:
            c1["s"] = c2["s"] = True
    curve_pts = np.zeros(N)
    for c in curves:
        if c["urban"]:
            continue
        w = {"kehre": 1.0, "eng": 1.0, "mittel": 1.0, "weit": 0.5}[c["cls"]] * min(c["angle"] / 60.0, 1.5)
        if c.get("s"):
            w *= 1.2
        if c["rough"]:
            w *= 0.5   # Eggeralm-Lehre: Kurven auf holprigem/engem Belag sind kein reiner Gewinn
        curve_pts[c["i"]] += w

    for c in curves:   # Spitzkehren brechen den Rhythmus (in der Gruppe faellt jeder auf Schritttempo)
        if c["cls"] == "kehre":
            events.append([c["i"], "kehre"])
            ev_w[c["i"]] += EVENT["kehre"][1]
    events.sort(key=lambda e: e[0])
    rough = [c for c in curves if c["rough"] and not c["urban"] and c["cls"] in ("kehre", "eng", "mittel")]
    grp = []
    for c in rough + [None]:
        if c is not None and grp and (c["i"] - grp[-1]["i"]) * STEP <= 1500:
            grp.append(c)
            continue
        if len(grp) >= 3:
            kehren = sum(1 for g in grp if g["cls"] == "kehre")
            mid = grp[len(grp) // 2]["i"]
            rough_notes_buf.append((grp[0]["i"], grp[-1]["i"], mid, len(grp), kehren))
        grp = [c] if c is not None else []

    # ------------------------------------------------ Geschwindigkeit / Zeit
    vdef = np.array([ROAD_VMAX.get(c, 70) for c in cls], float)
    vdef = np.where(urban & (vdef > 50), 50, vdef)
    vlim = np.where(np.isfinite(vlimit), np.minimum(vlimit, 130), vdef)
    # Kurvengeschwindigkeit ueber Querbeschleunigung (gemuetliche 2,5 m/s²), Kappen fuer
    # holprige/schmale Strassen, dann Brems-/Beschleunigungsphasen vor/nach jeder Kurve
    # (sonst mittelt sich eine Kehre einfach weg).
    vcurve = np.sqrt(2.5 / np.maximum(np.abs(kappa), 1e-5)) * 3.6
    rough_pt = np.isfinite(surf_score) & (surf_score < 50)
    vcap = np.where(rough_pt, 35.0, np.where(narrow, 50.0, 200.0))
    v = np.clip(np.minimum(np.minimum(vlim, vcurve), vcap), 5, 130) / 3.6
    for i in range(1, N):                      # beschleunigen ~1,5 m/s²
        v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * 1.5 * STEP))
    for i in range(N - 2, -1, -1):             # bremsen ~2,5 m/s²
        v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * 2.5 * STEP))
    v = v * 3.6
    T = np.cumsum(STEP / (v / 3.6) + ev_t)   # Fahrzeit bis Punkt i (s), ohne Pausen
    time_s = float(T[-1])

    # ------------------------------------------------ Schlenker (Groefelhof-Lehre)
    # Route verlaesst eine Bundes-/Landesstrasse fuer < 800 m und kehrt auf dieselbe
    # zurueck: bringt meist nur zwei Abbiegevorgaenge statt Fahrspass.
    notes = []
    k = 0
    while k < N - 1:
        key, rk = road_key[k], rank[k]
        if key and rk >= 4 and road_key[k + 1] != key:
            j = k + 1
            while j < N and road_key[j] != key and (j - k) * STEP <= 800:
                j += 1
            if j < N and road_key[j] == key and (j - k) * STEP >= 50 and rank[k + 1:j].max() < rk:
                mid = (k + j) // 2
                notes.append({"km": round(S[k] / 1000, 2), "lat": round(float(plat[mid]), 5), "lon": round(float(plon[mid]), 5),
                              "t": "detour", "label": f"Kurzer Schlenker abseits der {key}",
                              "text": f"{(j - k) * STEP:.0f} m auf einer Nebenstraße, dann zurück auf die {key} – "
                                      "zwei Abbiegevorgänge, vermutlich ohne Mehrwert."})
                k = j
                continue
        k += 1
    for a, b, mid, n_c, kehren in rough_notes_buf:
        notes.append({"km": round(S[a] / 1000, 2), "lat": round(float(plat[mid]), 5), "lon": round(float(plon[mid]), 5),
                      "t": "rough", "label": f"Kurvige Passage mit Haken (km {S[a]/1000:.1f}–{S[b]/1000:.1f})",
                      "text": f"{n_c} Kurven" + (f", davon {kehren} Spitzkehre{'n' if kehren != 1 else ''}" if kehren else "")
                              + " auf holprigem Belag oder schmaler Straße – reizvoll auf dem Papier, fordernd in der Praxis."})
    notes.sort(key=lambda n: n["km"])

    # ------------------------------------------------ POIs
    progress(90, "Sehenswertes wird eingeordnet")
    pois = []
    for el in pois_raw.get("elements", []):
        tg = el.get("tags", {})
        if "place" in tg:
            continue
        kind = None
        for (kk, vv), spec in POI_KIND.items():
            if tg.get(kk) == vv:
                kind = (kk, vv, spec)
                break
        if not kind:
            continue
        la, lo = (el["lat"], el["lon"]) if "lat" in el else (el.get("center", {}).get("lat"), el.get("center", {}).get("lon"))
        if la is None:
            continue
        px, py = proj.fwd(la, lo)
        j = int(ptree.nearest(Point(float(px), float(py))))
        off = math.hypot(P[j, 0] - px, P[j, 1] - py)
        label, score, group = kind[2]
        if group == "sight":
            # Bekanntheit (Wikipedia/Wikidata) zaehlt, Namenloses nur direkt am Strassenrand
            if tg.get("wikidata") or tg.get("wikipedia"):
                score += 1.5
            if not tg.get("name"):
                if off > 150:
                    continue
                score *= 0.5
            score *= 1.0 if off < 150 else (0.7 if off < 400 else 0.4)
        biker = "motorrad" in (tg.get("name", "") + tg.get("description", "")).lower() or tg.get("motorcycle") in ("yes", "designated")
        flags = poi.poi_flags(tg, group)
        extra = {}
        if group == "camp":
            extra = camp_extra(tg, la, lo, db)
            if extra is None:     # Wasser, das nicht zugaenglich ist (privat): gar nicht zeigen
                continue
        if group == "fuel":
            # Autobahn-Tankstelle (Raststaette, meist teuer): Route fuehrt dort auf der Autobahn vorbei
            # und die Tankstelle liegt direkt daneben. Autohoefe an der Ausfahrt liegen weiter weg.
            near = match[max(j - 3, 0):j + 4]
            on_mw = len(near) > 0 and all(m >= 0 and ways[m].get("tags", {}).get("highway") == "motorway" for m in near)
            autohof = "autohof" in (tg.get("name", "") + " " + tg.get("operator", "")).lower()
            flags["motorway"] = bool(on_mw and off <= 150 and not autohof)
            flags["cls"] = poi.fuel_class(tg, flags["motorway"])
        pois.append({"km": round(S[j] / 1000, 2), "lat": la, "lon": lo, "off": round(off), "group": group,
                     "label": label, "name": tg.get("name") or tg.get("brand") or "", "score": round(score, 1),
                     "wiki": bool(tg.get("wikidata") or tg.get("wikipedia")), "biker": bool(biker),
                     "tmin": round(float(T[j]) / 60), "ele": tg.get("ele"), "i": j,
                     "flags": flags, "osm": f'{el.get("type", "node")}/{el.get("id", "")}', **extra})
    if options.get("rig") and db is not None and n_local:
        pois += _detour_fuel(db, proj, P, plat, plon, S, T, cls, {p["osm"] for p in pois if p["group"] == "fuel"})
    pois.sort(key=lambda p: p["km"])
    for p in pois:
        if p["label"] == "Pass" and p["off"] <= 300 and p["wiki"]:
            notes.append({"km": p["km"], "lat": p["lat"], "lon": p["lon"], "t": "pass",
                          "label": f"Bekannte Passstraße: {p['name'] or 'Pass'}" + (f" ({p['ele']} m)" if p["ele"] else ""),
                          "text": "An schönen Tagen viel Ausflugsverkehr – ob der Pass zum Erlebnis oder zur Geduldsprobe wird, "
                                  "hängt von Tag und Uhrzeit ab. Früh starten hilft."})
    notes.sort(key=lambda n: n["km"])
    # Doppelte (Node + Way gleichen Namens) entfernen
    seen, uniq = set(), []
    for p in pois:
        key = (p["label"], p["name"], round(p["km"]))
        if p["name"] and key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    pois = uniq
    shop_pois = []
    if options.get("rig"):   # Rig: Laeden an der Route (Erweiterung, siehe hooks.py)
        for fn in hooks.all_("analyze.extra_pois"):
            shop_pois += fn(plat, plon, notes)

    # ------------------------------------------------ Abschnitte
    progress(95, "Abschnitte werden zusammengestellt")
    cstep = int(options.get("coord_step", 2))   # Kartenpunkte je Abschnitt (Reisen: groeber)
    n_seg = max(1, int(round(total / SEG_LEN)))
    bounds = np.linspace(0, N - 1, n_seg + 1).astype(int)
    win = int(1000 / STEP)  # Fluss im +-1-km-Fenster
    ev_cum = np.concatenate([[0], np.cumsum(ev_w)])
    segments = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        b = max(b, a + 1)
        sl = slice(a, b + 1)
        length_km = (S[b] - S[a]) / 1000
        c0, c1 = max(a - win, 0), min(b + win, N - 1)
        dens = (ev_cum[c1 + 1] - ev_cum[c0]) / max((S[c1] - S[c0]) / 1000, 0.1)
        urb = float(urban[sl].mean())
        fluss = 100 * math.exp(-dens * 0.9) * (1 - 0.5 * urb)
        cp = curve_pts[sl].sum() / max(length_km, 0.05)
        kurven = 100 * (1 - math.exp(-cp / 2.0))
        ss = surf_score[sl]
        belag = float(np.nanmean(ss)) if np.isfinite(ss).any() else None
        lc = Counter(c for c in land[sl] if c)
        lval = (sum(LAND_VALUE[c] * n for c, n in lc.items()) / sum(lc.values())) if lc else None
        if lval is not None and near_river[sl].mean() > 0.5:
            lval = min(1.0, lval + 0.15)
        cc = Counter(cls[sl])
        road = cc.most_common(1)[0][0]
        strasse = sum(ROAD_SCORE.get(c, 50) * n for c, n in cc.items()) / sum(cc.values())
        vl = vlimit[sl]
        segments.append({
            "km0": round(S[a] / 1000, 2), "km1": round(S[b] / 1000, 2),
            "c": [[round(float(plat[i]), 5), round(float(plon[i]), 5)] for i in range(a, b, cstep)] +
                 [[round(float(plat[b]), 5), round(float(plon[b]), 5)]],
            "road": road, "roadl": ROAD_LABEL.get(road, road),
            "urban": urb > 0.5,
            "surf": Counter(x for x in surf_lbl[sl] if x).most_common(1)[0][0] if any(x for x in surf_lbl[sl]) else None,
            "ms": int(np.nanmedian(vl)) if np.isfinite(vl).any() else None,
            "land": lc.most_common(1)[0][0] if lc else None,
            "river": bool(near_river[sl].mean() > 0.5),
            "sc": {
                "fluss": round(fluss), "kurven": round(kurven),
                "belag": None if belag is None else round(belag),
                "landschaft": None if lval is None else round(lval * 100),
                "strasse": round(strasse), "ort": round((1 - urb) * 100),
            },
        })

    # ------------------------------------------------ Kennzahlen gesamt
    km = total / 1000
    rural = ~urban
    rural_km = rural.sum() * STEP / 1000
    w_events = float(ev_w.sum())
    # laengster ungestoerter Abschnitt (Ereignisse ab Gewicht 0.5)
    ev_strong = sorted(i for i, t_ in events if EVENT[t_][1] >= 0.5)
    marks = [0] + ev_strong + [N - 1]
    gaps = [(S[b] - S[a], a, b) for a, b in zip(marks, marks[1:])]
    g_len, g_a, g_b = max(gaps)
    avg_free = km / (len(ev_strong) + 1)
    mild = sum(EVENT[t_][1] for _, t_ in events if EVENT[t_][1] < 0.5) / km
    fluss_score = 100 * (1 - math.exp(-avg_free / 5.0)) * math.exp(-mild * 0.4)

    curve_rural = [c for c in curves if not c["urban"]]
    cpkm = curve_pts.sum() / max(rural_km, 1)
    kurven_score = 100 * (1 - math.exp(-cpkm / 1.2))
    wlen = min(int(10000 / STEP), N - 1)
    cc_cum = np.concatenate([[0], np.cumsum(curve_pts)])
    wsum = cc_cum[wlen:] - cc_cum[:-wlen]
    wi0 = int(np.argmax(wsum))
    curvy_win = {"km0": round(S[wi0] / 1000, 1), "km1": round(S[min(wi0 + wlen, N - 1)] / 1000, 1),
                 "pts": round(float(wsum[wi0]), 1)}

    urban_share = float(urban.mean())
    ort_score = 100 * (1 - urban_share) ** 1.5

    known = np.isfinite(surf_score)
    coverage = float(known.mean())
    belag_score = float(np.nanmean(surf_score)) if known.any() else None
    surf_mix = Counter(x for x in surf_lbl if x)
    surf_mix = {k_: round(v_ / N * 100, 1) for k_, v_ in surf_mix.most_common(6)}

    lcount = Counter(c for c in land if c)
    land_cov = sum(lcount.values()) / N
    land_mix = {k_: round(v_ / N * 100, 1) for k_, v_ in lcount.most_common()}
    land_score = None
    if lcount:
        base = sum(LAND_VALUE[c] * n for c, n in lcount.items()) / sum(lcount.values())
        probs = np.array(list(lcount.values()), float) / sum(lcount.values())
        variety = float(-(probs * np.log(probs)).sum() / math.log(6))
        land_score = 100 * min(1.0, base * 0.85 + variety * 0.15 + 0.1 * near_river.mean())

    road_mix = Counter(cls)
    road_mix = {ROAD_LABEL.get(k_, k_): round(v_ / N * 100, 1) for k_, v_ in road_mix.most_common()}
    strasse_score = float(np.mean([ROAD_SCORE.get(c, 50) for c in cls]))

    sights = [p for p in pois if p["group"] == "sight"]
    worth = sorted((p for p in sights if p["score"] >= 1.5), key=lambda p: -p["score"])
    # abnehmender Grenznutzen: der zehnte Aussichtspunkt macht die Tour nicht zehnmal besser
    sight_pts = sum(p["score"] * 0.85 ** k for k, p in enumerate(worth))
    sight_score = 100 * (1 - math.exp(-(sight_pts / max(km, 20) * 100) / 30.0))

    food = [p["km"] for p in pois if p["group"] == "food"]
    fuel = [p["km"] for p in pois if p["group"] == "fuel" and not p["flags"].get("detour_km")]
    max_food_gap = float(np.diff([0.0] + sorted(food) + [km]).max())
    pause_score = 100.0 if max_food_gap <= 30 else max(0.0, 100 - (max_food_gap - 30) * 1.0)

    free_share = float(((v >= 85) & rural).mean())
    asc = None
    if ele is not None:
        de = np.diff(ele)
        asc = round(float(de[de > 0].sum()))

    ev_count = Counter(t_ for _, t_ in events)
    stats = {
        "km": round(km, 1), "time_h": round(time_s / 3600, 2), "avg_kmh": round(km / (time_s / 3600), 0),
        "ascent": asc, "urban_share": round(urban_share * 100, 1), "towns": len(towns),
        "events": {EVENT[k_][0]: n for k_, n in ev_count.most_common()},
        "events_per_100km": round(w_events / km * 100, 1),
        "longest_free": {"km0": round(S[g_a] / 1000, 1), "km1": round(S[g_b] / 1000, 1), "len": round(g_len / 1000, 1)},
        "avg_free_km": round(avg_free, 1),
        "curves": len(curve_rural), "curves_urban": len(curves) - len(curve_rural),
        "curves_by_cls": dict(Counter(c["cls"] for c in curve_rural)),
        "s_curves": sum(1 for c in curve_rural if c.get("s")) // 2,
        "curves_per_km": round(len(curve_rural) / max(rural_km, 1), 2),
        "curvy_window": curvy_win,
        "surface_mix": surf_mix, "surface_coverage": round(coverage * 100),
        "land_mix": land_mix, "land_coverage": round(land_cov * 100),
        "river_share": round(float(near_river.mean()) * 100),
        "road_mix": road_mix, "free_share": round(free_share * 100),
        "max_food_gap": round(max_food_gap, 1), "fuel_km": fuel, "matched": round(matched_share * 100),
        "sights": len(worth), "source": source, "reconstructed": recon, "missing": info.get("missing", []),
        "osm_date": (db.meta.get("built", "")[:10] if db is not None and n_local else None),
    }

    axes = {
        "fluss": {"score": round(fluss_score), "label": "Fahrfluss"},
        "kurven": {"score": round(kurven_score), "label": "Kurven"},
        "ort": {"score": round(ort_score), "label": "Freie Strecke"},
        "belag": {"score": None if belag_score is None else round(belag_score), "label": "Belag",
                  "note": f"Datenlage {coverage*100:.0f} %"},
        "landschaft": {"score": None if land_score is None else round(land_score), "label": "Landschaft"},
        "strasse": {"score": round(strasse_score), "label": "Kleine Straßen"},
        "sight": {"score": round(sight_score), "label": "Sehenswertes"},
        "pause": {"score": round(pause_score), "label": "Pausen"},
    }

    food_t = sorted(p["tmin"] for p in pois if p["group"] == "food")
    gaps_t = np.diff([0] + food_t + [round(time_s / 60)])
    rhythm = []
    for t_ in food_t:   # moegliche Pausen: ab 20 min Fahrt, dann mind. 30 min Abstand
        if t_ >= 20 and (not rhythm or t_ - rhythm[-1] >= 30) and t_ <= time_s / 60 - 15:
            rhythm.append(t_)
    stats["food_rhythm_min"] = rhythm
    stats["food_max_gap_min"] = int(max(gaps_t)) if len(gaps_t) else None
    stats["kehren"] = sum(1 for c in curve_rural if c["cls"] == "kehre")
    stats["rough_curves"] = sum(1 for c in curve_rural if c["rough"])
    hl = _highlights(stats, towns, worth, curve_rural, coverage, notes)

    rig_data = None
    if options.get("rig"):
        progress(97, "Höhen, Breiten, Gewichte und Steigungen werden geprüft")
        ctx = {"S": S, "plat": plat, "plon": plon, "N": N, "STEP": STEP, "ways": ways, "match": match,
               "nodes": nodes, "node_xy": node_xy, "node_ways": node_ways, "nearest_idx": _nearest_idx,
               "curves": curves, "kappa": kappa, "vlim": vlim, "cls": cls, "bounds": list(zip(bounds[:-1], bounds[1:])),
               "region_of": _region_of}
        collect = hooks.one("analyze.vehicle")
        if collect is None:
            raise RuntimeError("Fahrzeugpruefung nicht angemeldet (RA_EXTENSIONS)")
        rig_data = collect(ctx, want_elevation=options.get("elevation", True),
                               progress=lambda p, m: progress(97, m))
        rig_data["lez"] = lez.on_route(plat, plon, S / 1000)   # Umweltzonen, None = kein Datenstand
        for seg, extra in zip(segments, rig_data.pop("segments")):
            seg.update(extra)
        if unsure:
            def inside(it):
                return any(it.get("km0", -1) < b and it.get("km1", it.get("km0", -1)) > a for a, b in unsure)

            n0 = len(rig_data["restrictions"])
            rig_data["restrictions"] = [it for it in rig_data["restrictions"] if not inside(it)]
            km_u = sum(b - a for a, b in unsure)
            warnings.append(f"{km_u:.0f} km der Strecke liegen in Aufzeichnungslücken über {GAP_M // 1000} km – "
                            f"dort ist der genaue Verlauf unbekannt, {n0 - len(rig_data['restrictions'])} mögliche "
                            f"Engstellen/Verbote wurden deshalb nicht gewertet.")
            rig_data["unsure"] = [[round(a, 2), round(b, 2)] for a, b in unsure]
    quality = _quality(stats, match, ways, pois, ele is not None if rig_data is None else rig_data["elevation"],
                       rig_data is not None)
    progress(100, "fertig")
    return {
        "name": name or filename.rsplit(".", 1)[0] or "Route",
        "rig": rig_data, "quality": quality,
        "stats": stats, "axes": axes, "highlights": hl, "warnings": warnings,
        "segments": segments, "towns": towns,
        "places": [{"name": q["name"], "km": round(float(S[q["i"]]) / 1000, 2), "lat": round(float(plat[q["i"]]), 5),
                    "lon": round(float(plon[q["i"]]), 5), "r": q["r"]} for q in near_places],
        "events": [{"km": round(S[i] / 1000, 2), "lat": round(float(plat[i]), 5), "lon": round(float(plon[i]), 5),
                    "t": t_, "label": EVENT[t_][0], "w": EVENT[t_][1]} for i, t_ in events],
        "curves": [{"km": round(S[c["i"]] / 1000, 2), "lat": round(float(plat[c["i"]]), 5),
                    "lon": round(float(plon[c["i"]]), 5), "r": c["r"], "angle": c["angle"], "cls": c["cls"],
                    "s": bool(c.get("s")), "urban": c["urban"], "rough": c["rough"], "v": c["v"]} for c in curves],
        "notes": notes,
        "pois": sorted([{k_: v_ for k_, v_ in p.items() if k_ != "i"} for p in pois] + shop_pois, key=lambda p: p["km"]),
        "elev": None if ele is None else [[round(S[i] / 1000, 2), round(float(ele[i]))] for i in range(0, N, max(1, N // 400))],
    }


SPEED_KEYS = ("maxspeed", "source:maxspeed", "zone:maxspeed", "maxspeed:type", "zone:traffic")
WIDTH_KEYS = ("width", "est_width", "lanes")


def _quality(stats, match, ways, pois, has_ele, for_rig):
    """Wie vollstaendig sind die Kartendaten fuer diese Route? Anteile in %, dazu eine Gesamtnote.
    Fehlende Angaben heissen nicht „falsch“, aber die Bewertung stuetzt sich dort auf Annahmen."""
    m = np.asarray(match)
    hit = m[m >= 0]
    def share(keys):
        if not len(hit):
            return 0
        ok = {i for i in set(hit.tolist()) if any(k in ways[i].get("tags", {}) for k in keys)}
        return round(float(np.isin(hit, list(ok)).mean()) * 100 * len(hit) / len(m))
    q = {"matched": stats["matched"], "surface": stats["surface_coverage"], "speed": share(SPEED_KEYS),
         "elevation": bool(has_ele), "source": stats["source"], "reconstructed": stats["reconstructed"],
         "osm_date": stats["osm_date"]}
    parts = [(q["matched"], 0.5), (q["surface"], 0.25), (q["speed"], 0.25)]
    if for_rig:
        q["width"] = share(WIDTH_KEYS)
        parts = [(q["matched"], 0.4), (q["surface"], 0.15), (q["speed"], 0.15), (q["width"], 0.1),
                 (100 if has_ele else 0, 0.2)]
        fuel = [p for p in pois if p["group"] == "fuel" and not p["flags"].get("detour_km")]
        camps = [p for p in pois if p["group"] == "camp" and (p.get("flags") or {}).get("kind")]
        def pct(lst, f):
            return round(100 * sum(1 for p in lst if f(p)) / len(lst)) if lst else None

        q["fuel"] = {"n": len(fuel), "diesel": pct(fuel, lambda p: p["flags"].get("diesel_known")),
                     "adblue": pct(fuel, lambda p: p["flags"].get("adblue_known")),
                     "hours": pct(fuel, lambda p: p["flags"].get("hours"))}
        q["camps"] = {"n": len(camps), "details": pct(camps, lambda p: any(
            p["flags"].get(k) not in (None, "") for k in ("fee", "capacity", "maxlength", "hours")))}
    q["score"] = round(sum(v * w for v, w in parts))
    q["grade"] = "gut" if q["score"] >= 80 else ("brauchbar" if q["score"] >= 55 else "dünn")
    return q


DETOUR_RADIUS = 3000.0   # Tankstellen so weit von einer Autobahn-Ausfahrt (dort oft deutlich guenstiger)
DETOUR_ROAD = 1.3        # Strassenweg ~ 1,3 x Luftlinie
DETOUR_RAMP = 1.0        # ab- und wieder auffahren (km)
DETOUR_PER_EXIT = 3      # je Ausfahrt nur die vielversprechendsten (sonst ~1.700 extra auf 4.000 km)
DETOUR_RANK = {"discount": 0, "frei": 1, "autohof": 2, "marke": 3}


def _detour_fuel(db, proj, P, plat, plon, S, T, cls, known):
    """Rig: Tankstellen bis 3 km hinter einer Autobahn-Ausfahrt als moeglicher Abstecher.
    flags.detour_km = geschaetzter Umweg hin + zurueck ab der Ausfahrt (Luftlinie x 1,3 + Rampen)."""
    mw = np.where(cls == "motorway")[0][::25]          # alle 250 m auf der Autobahn
    if not len(mw):
        return []
    ramps = db.ramps_near(plat[mw], plon[mw], proj)
    if not len(ramps):
        return []
    rx, ry = proj.fwd(ramps[:, 0], ramps[:, 1])
    ramp_tree = STRtree(shapely.points(np.column_stack([rx, ry])))
    out = []
    for el, _d in db.fuel_near(ramps[:, 0], ramps[:, 1], proj, DETOUR_RADIUS):
        osm = f'{el["type"]}/{el["id"]}'
        if osm in known:
            continue
        la, lo = (el["lat"], el["lon"]) if "lat" in el else (el["center"]["lat"], el["center"]["lon"])
        px, py = proj.fwd(la, lo)
        pt = Point(float(px), float(py))
        k = int(ramp_tree.nearest(pt))
        d_ramp = float(math.hypot(rx[k] - px, ry[k] - py))
        j = int(mw[int(np.argmin(np.hypot(P[mw, 0] - rx[k], P[mw, 1] - ry[k])))])
        off = float(math.hypot(P[j, 0] - px, P[j, 1] - py))
        if off <= 500 or d_ramp > DETOUR_RADIUS:
            continue
        tg = el.get("tags", {})
        flags = poi.poi_flags(tg, "fuel")
        if flags["card_only"]:
            continue
        flags.update(motorway=False, cls=poi.fuel_class(tg, False),
                     detour_km=round(2 * d_ramp * DETOUR_ROAD / 1000 + DETOUR_RAMP, 1))
        out.append({"km": round(S[j] / 1000, 2), "lat": la, "lon": lo, "off": round(off), "group": "fuel",
                    "label": "Tankstelle", "name": tg.get("name") or tg.get("brand") or "", "score": 0,
                    "wiki": False, "biker": False, "tmin": round(float(T[j]) / 60), "ele": None, "i": j,
                    "flags": flags, "osm": osm})
    # je Ausfahrt (Streckenkilometer, auf 5 km gebuendelt) die guenstigste Art zuerst, dann der kuerzeste Umweg
    best = {}
    for p in sorted(out, key=lambda p: (DETOUR_RANK.get(p["flags"]["cls"], 9), p["flags"]["detour_km"])):
        if len(best.setdefault(int(p["km"] // 5), [])) < DETOUR_PER_EXIT:
            best[int(p["km"] // 5)].append(p)
    return [p for ps in best.values() for p in ps]


def camp_extra(tg, lat, lon, db):
    """Zugangs-Einstufung fuer Platz-, Wasser- und Entsorgungs-Orte: {"water_kind": public|guests|tap|unknown|None};
    None = Wasser, das nicht zugaenglich ist (wird weggelassen). Alte Regionen (Schema < 4) -> unknown."""
    if tg.get("tourism") in ("caravan_site", "camp_site"):
        return {"water_kind": poi.water_kind(tg, False)}
    amen = tg.get("amenity")
    if amen not in ("water_point", "drinking_water", "sanitary_dump_station"):
        return {"water_kind": None}
    if tg.get("access") in ("private", "no", "permit"):
        return None
    inside = db.in_camp(lat, lon) if db is not None else None
    if amen == "sanitary_dump_station":
        # Entsorgung: dieselbe Zugangsfrage wie beim Wasser (auf dem Platz bzw. nur fuer Kunden = Gaeste)
        if inside or tg.get("access") == "customers":
            return {"water_kind": "guests"}
        return {"water_kind": "unknown" if inside is None else "public"}
    if inside is None:
        return {"water_kind": "unknown"}
    return {"water_kind": poi.water_kind(tg, inside)}


_osm_ref = poi.osm_ref


def mark_last_shop(stages):
    """Je Etappe der letzte Laden bis zum Etappenziel (hoechster km <= Etappenlaenge) als st["last_shop"]."""
    for st in stages:
        length = float((st.get("stats") or {}).get("km") or 0)
        cand = [p for p in st.get("pois", []) if p.get("group") == "shop" and p["km"] <= length]
        if not cand:
            continue
        p = max(cand, key=lambda q: q["km"])
        f = p.get("flags") or {}
        st["last_shop"] = {"name": p["name"], "label": p["label"], "kind": f.get("kind"),
                           "km_before": round(length - p["km"], 1), "hours": f.get("hours")}


def mark_near_stage(stages, km=10.0):
    """Plaetze (Gruppe camp) bis km vor bzw. nach einem Etappenziel bekommen near_stage = Etappennummer."""
    for i, st in enumerate(stages):
        length = float((st.get("stats") or {}).get("km") or 0)
        for p in st.get("pois", []):
            if p.get("group") != "camp":
                continue
            if p["km"] >= length - km:
                p["near_stage"] = i + 1
            elif i > 0 and p["km"] <= km:
                p["near_stage"] = i


def _curve_speed(r, rough, vlimit, road_cls):
    """Richtgeschwindigkeit fuer eine Kurve aus dem Radius: v = sqrt(a_quer * r).
    Gemuetlich 2,0 m/s² (Sozius/Gruppe) bis zuegig 4,0 m/s²; holpriger/schmaler Belag
    drueckt den oberen Wert; gedeckelt durch Tempolimit und Strassenklasse.
    -> (von, bis) in km/h, auf 5 gerundet."""
    lo = math.sqrt(2.0 * r) * 3.6
    hi = math.sqrt(4.0 * r) * 3.6 * (0.8 if rough else 1.0)
    cap = ROAD_VMAX.get(road_cls, 100)
    if vlimit is not None and np.isfinite(vlimit):
        cap = min(cap, float(vlimit))
    lo, hi = min(lo, cap), min(hi, cap)
    def rnd(x):
        return int(max(10, round(x / 5) * 5))

    lo, hi = rnd(lo), rnd(hi)
    return [lo, max(hi, lo)]


def _clean_mask(mask, min_run, max_gap):
    m = np.asarray(mask, bool).copy()
    if max_gap > 0:
        for a, b in _runs(~m):
            if a > 0 and b < len(m) and b - a <= max_gap:
                m[a:b] = True
    for a, b in _runs(m):
        if b - a < min_run:
            m[a:b] = False
    return m


def _fmt_km(x):
    return f"{x:.0f}" if x >= 10 else f"{x:.1f}".replace(".", ",")


def _hm(minutes):
    return f"{int(minutes) // 60}:{int(minutes) % 60:02d} h"


def _highlights(st, towns, sights, curves, coverage, notes):
    """Kurze Saetze in der Sprache des Projektbuchs: zeigen, was moeglich ist."""
    out = []
    lf = st["longest_free"]
    out.append(f"Längster flüssiger Abschnitt: {_fmt_km(lf['len'])} km ohne Ampel oder Wartepflicht "
               f"(km {_fmt_km(lf['km0'])}–{_fmt_km(lf['km1'])}).")
    out.append(f"Im Schnitt {_fmt_km(st['avg_free_km'])} km freie Fahrt zwischen zwei Unterbrechungen.")
    free = 100 - st["urban_share"]
    if towns:
        uniq = list(dict.fromkeys(t["name"] for t in towns if t["name"] != "Ortslage"))
        names = ", ".join(uniq[:6]) + (" …" if len(uniq) > 6 else "")
        out.append(f"{free:.0f} % der Strecke außerorts; {len(towns)} Ortsdurchfahrten"
                   + (f" ({names})." if names else "."))
    else:
        out.append("Die Strecke bleibt komplett außerorts.")
    if curves:
        cw = st["curvy_window"]
        eng = st["curves_by_cls"].get("eng", 0)
        kehren = st["kehren"]
        parts = []
        if kehren:
            parts.append(f"{kehren} Spitzkehre{'n' if kehren != 1 else ''}")
        if eng:
            parts.append(f"{eng} enge")
        if st["s_curves"]:
            parts.append(f"{st['s_curves']} Wechselkurve{'n' if st['s_curves'] != 1 else ''}")
        lst = (", ".join(parts[:-1]) + " und " + parts[-1]) if len(parts) > 1 else (parts[0] if parts else "")
        out.append(f"{len(curves)} Kurven außerorts" + (f", davon {lst}" if lst else "")
                   + f"; die kurvigsten 10 km liegen bei km {_fmt_km(cw['km0'])}–{_fmt_km(cw['km1'])}.")
    else:
        out.append("Ein Abschnitt zum Gleiten: kaum Kurven, viel Geradeaus.")
    if st["surface_mix"]:
        top, share = next(iter(st["surface_mix"].items()))
        out.append(f"Belag überwiegend {top} ({share:.0f} %); für {st['surface_coverage']} % der Strecke ist der Belag erfasst.")
    if sights:
        best = sorted(sights, key=lambda p: -p["score"])[:3]
        out.append("Lohnende Stopps: " + "; ".join(
            f"{p['name'] or p['label']} ({p['label']}, km {_fmt_km(p['km'])})" for p in best) + ".")
    if st["rough_curves"]:
        n_r = st["rough_curves"]
        out.append(f"{n_r} Kurve{'n liegen' if n_r != 1 else ' liegt'} auf holprigem Belag oder schmaler Straße – "
                   f"{'die zählen' if n_r != 1 else 'die zählt'} hier nur halb.")
    detours = [n for n in notes if n["t"] == "detour"]
    if detours:
        out.append(f"{len(detours)} kurze{'r' if len(detours) == 1 else ''} Schlenker abseits der Hauptstraße "
                   f"(erster bei km {_fmt_km(detours[0]['km'])}) – lohnt ein Blick, ob er gewollt ist.")
    if st["food_rhythm_min"]:
        out.append("Pausenrhythmus möglich – Einkehr an der Strecke z. B. nach "
                   + ", ".join(_hm(t) for t in st["food_rhythm_min"][:5]) + " Fahrzeit.")
    if st["max_food_gap"] <= 40:
        out.append(f"Einkehr ist gut verteilt – höchstens {_fmt_km(st['max_food_gap'])} km "
                   f"({_hm(st['food_max_gap_min'])}) bis zum nächsten Café oder Restaurant.")
    else:
        out.append(f"Längster Abschnitt ohne Einkehr direkt an der Strecke: {_fmt_km(st['max_food_gap'])} km "
                   f"({_hm(st['food_max_gap_min'])}) – Pause vorher einplanen.")
    if st["river_share"] >= 15:
        out.append(f"Auf {st['river_share']} % der Strecke ist ein Fluss oder Kanal in der Nähe.")
    return out


def run_trip(stages, gname, filename, progress, info, options, finish_missing=list):
    """Reise mit Etappen: jede Etappe einzeln analysieren (gleiche Engine), zusammen zurueckgeben.
    finish_missing: ergaenzt die fehlenden Regionen (Webseite: Groesse und Wartezeit). Seit 0.46.6 hier statt in main,
    damit der Navi-Paketbau (package.py) main nicht importieren muss."""
    if len(stages) > MAX_STAGES:
        raise AnalyzeError(f"Zu viele Etappen ({len(stages)}, max. {MAX_STAGES}).")
    n = len(stages)
    results, missing = [], {}
    for k, (name, nights, pts) in enumerate(stages):
        label = name or f"Etappe {k + 1}"

        def sp(p, msg, k=k):
            progress((k + (p or 0) / 100) / n * 100, f"Etappe {k + 1}/{n}: {msg}")

        r = analyze(stage_gpx(pts, label), f"etappe{k + 1}.gpx", sp, info,
                            {**(options or {}), "coord_step": 5})
        r["stage"] = {"n": k + 1, "name": label, "nights": nights}
        for m in r.get("missing") or []:
            if m.get("region"):
                e = missing.setdefault(m["region"], dict(m))
                e["share"] = max(e["share"], m["share"])
        results.append(r)
    mark_near_stage(results)
    mark_last_shop(results)
    return {"trip": True, "name": gname or filename.rsplit(".", 1)[0], "file": filename, "stages": results,
            "missing": finish_missing(list(missing.values()))}
