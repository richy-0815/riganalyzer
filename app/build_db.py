"""Baut die lokale OSM-Datenbank (SQLite + R-Tree) aus Geofabrik-Extrakten.

Nur was RideAnalyzer braucht: Strassen (mit Knoten-IDs fuer Kreuzungen),
Ampeln/Schilder/Bahnuebergaenge, POIs + Orte, Landnutzung (vereinfacht), Fluesse.
Abdeckung = Geofabrik-.poly je Region.

Aufruf:  python build_db.py <ziel.sqlite> region [region ...]
Region:  Geofabrik-Pfad, z. B. europe/germany/brandenburg
"""

import json
import logging
import os
import sqlite3
import sys
import time
from array import array

import httpx
import osmium
from shapely import wkb
from shapely.geometry import MultiPolygon, Polygon

log = logging.getLogger("build_db")

GEOFABRIK = os.environ.get("GEOFABRIK_URL", "https://download.geofabrik.de")
UA = "RideAnalyzer-Prototyp/0.1 (+https://rideanalyzer.petrick.top)"

ROAD_EXCLUDE = {"footway", "path", "cycleway", "steps", "pedestrian", "bridleway", "corridor",
                "elevator", "platform", "proposed", "construction", "bus_stop", "via_ferrata",
                "raceway", "abandoned", "razed", "services", "rest_area", "bus_guideway",
                "escape", "busway", "ladder", "emergency_bay"}
# Inhaltsversion der DB – hochzaehlen, wenn sich Inhalte aendern (Regionen werden dann neu gebaut)
# 4: Platz-Merkmale, Trinkwasser, Camp-Flaechen (land.cls="tourism=camp")
# 3: Koordinaten als int32 (1e-7 Grad, wie OSM), ohne Einfahrten/Parkgassen/Feldwege
SCHEMA_VERSION = 4
COORD = 1e7
# fuer Motorrad/Womo ohne Belang, machen aber einen grossen Teil der Wege aus
SKIP_SERVICE = {"driveway", "parking_aisle", "drive-through", "emergency_access"}
ROAD_TAGS = ("highway", "maxspeed", "source:maxspeed", "zone:maxspeed", "zone:traffic",
             "maxspeed:type", "surface", "smoothness", "junction", "priority_road", "name", "ref",
             "oneway", "width", "est_width", "lanes",
             # ab Schema 2: alles, was ein Wohnmobil/Gespann betrifft (RigAnalyzer)
             "maxheight", "maxheight:physical", "maxwidth", "maxwidth:physical", "maxweight",
             "maxweightrating", "maxweight:hgv", "maxaxleload", "maxlength", "hgv", "hgv:conditional",
             "motor_vehicle", "motorcar", "vehicle", "access", "trailer", "caravan", "motorhome",
             "toll", "toll:hgv", "tunnel", "bridge", "incline", "narrow", "passing_places")
RESTRICT_NODE = ("maxheight", "maxheight:physical", "maxwidth", "maxwidth:physical", "maxweight")
CTRL_HW = {"traffic_signals", "stop", "give_way", "mini_roundabout"}
CTRL_TAGS = ("highway", "crossing", "railway", "direction", "traffic_signals:direction",
             "stop:direction", "give_way:direction", "barrier", *RESTRICT_NODE)
POI = {
    "tourism": {"viewpoint", "attraction", "museum", "picnic_site", "zoo"},
    "historic": {"castle", "ruins", "monument", "manor", "fort", "city_gate", "monastery",
                 "archaeological_site", "windmill", "watermill"},
    "man_made": {"tower", "lighthouse", "windmill", "watermill"},
    "natural": {"peak", "waterfall", "beach"},
    "amenity": {"cafe", "restaurant", "biergarten", "ice_cream", "fuel", "sanitary_dump_station",
                "water_point", "drinking_water"},
    "place": {"city", "town", "village", "hamlet", "suburb"},
    "mountain_pass": {"yes"},
    # Wohnmobil (RigAnalyzer) – tourism-Werte oben ergaenzt
    "highway": {"services", "rest_area"},
}
POI["tourism"] |= {"caravan_site", "camp_site"}
POI_TAGS = ("name", "brand", "wikidata", "wikipedia", "tower:type", "motorcycle", "description", "ele",
            "fuel:diesel", "fuel:adblue", "fuel:HGV_diesel", "fuel:lpg", "hgv", "opening_hours",
            "motorhome", "caravans", "capacity", "fee", "sanitary_dump_station", "water_point",
            "power_supply", "maxlength", "operator",
            # Stell-/Campingplaetze (Schema 4): Filter Groesse, Kosten/Zeiten, Komfort, Zugang
            "maxweight", "charge", "seasonal", "shower", "toilets", "internet_access", "dog", "laundry",
            "drinking_water", "website", "contact:website", "phone", "contact:phone", "access",
            # (ohne Schema-Wechsel: kommen mit dem naechsten Monats-Neubau je Region dazu)
            "maxheight", "surface", "stars", "reservation", "email", "contact:email", "check_date", "survey:date",
            "description:de", "image", "wikimedia_commons", "addr:city", "tents", *POI.keys())
LAND = {
    "landuse": {"forest", "farmland", "meadow", "grass", "orchard", "vineyard", "residential",
                "industrial", "commercial", "retail", "farmyard", "allotments", "quarry",
                "reservoir", "basin"},
    "natural": {"wood", "water", "scrub", "heath", "wetland", "grassland", "beach"},
}
SIMPLIFY = 0.00012   # ~10 m
MIN_AREA = 1e-7      # ~750 m² – Kleinstflaechen (Slowenien: Ackerparzellen) weglassen


def _sub(tags, keys):
    return {k: tags[k] for k in keys if k in tags}


def _is_poi(tags):
    for k, vals in POI.items():
        v = tags.get(k)
        if v in vals:
            if k == "man_made" and v == "tower" and tags.get("tower:type") != "observation":
                return False
            if k == "natural" and v == "beach" and "name" not in tags:
                return False
            return True
    return False


def _land_class(tags):
    # Campingplatz-/Stellplatz-Flaechen: fuer „liegt die Zapfstelle auf dem Gelaende?“ (Schema 4)
    if tags.get("tourism") in ("camp_site", "caravan_site"):
        return "tourism=camp"   # mit "=": die Landschafts-Bewertung zerlegt jede Klasse daran
    for k, vals in LAND.items():
        if tags.get(k) in vals:
            return f"{k}={tags[k]}"
    return None


def download(region, workdir):
    os.makedirs(workdir, exist_ok=True)
    name = region.rsplit("/", 1)[-1]
    out = {}
    with httpx.Client(timeout=600, headers={"User-Agent": UA}, follow_redirects=True) as c:
        for suffix in ("-latest.osm.pbf", ".poly"):
            dst = os.path.join(workdir, name + suffix)
            tmp = dst + ".part"
            log.info("Lade %s%s", region, suffix)
            with c.stream("GET", f"{GEOFABRIK}/{region}{suffix}") as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            os.replace(tmp, dst)
            out[suffix] = dst
    return out["-latest.osm.pbf"], out[".poly"]


def _index_file():
    """Pfad des Knoten-Index, falls dateibasiert (OSM_INDEX=sparse_file_array,<pfad>)."""
    idx = os.environ.get("OSM_INDEX", "flex_mem")
    return idx.split(",", 1)[1] if "_file_array," in idx else None


def parse_poly(path):
    """Osmosis-.poly -> shapely (Multi)Polygon."""
    polys, holes, outers = [], [], []
    with open(path) as f:
        lines = [line.strip() for line in f if line.strip()]
    i = 1
    while i < len(lines):
        head = lines[i]
        if head == "END":
            break
        pts = []
        i += 1
        while lines[i] != "END":
            x, y = map(float, lines[i].split()[:2])
            pts.append((x, y))
            i += 1
        i += 1
        (holes if head.startswith("!") else outers).append(pts)
    for o in outers:
        p = Polygon(o)
        for h in holes:
            hp = Polygon(h)
            if p.contains(hp.representative_point()):
                p = p.difference(hp)
        polys.append(p)
    return MultiPolygon([g for p in polys for g in getattr(p, "geoms", [p])])


SCHEMA = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE coverage(region TEXT PRIMARY KEY, wkb BLOB);
CREATE TABLE road(id INTEGER PRIMARY KEY, tags TEXT, nodes BLOB, coords BLOB);
CREATE VIRTUAL TABLE road_ix USING rtree(id, minlon, maxlon, minlat, maxlat);
CREATE TABLE ctrl(id INTEGER PRIMARY KEY, lat REAL, lon REAL, tags TEXT);
CREATE VIRTUAL TABLE ctrl_ix USING rtree(id, minlon, maxlon, minlat, maxlat);
CREATE TABLE poi(id INTEGER PRIMARY KEY, osm TEXT UNIQUE, lat REAL, lon REAL, tags TEXT);
CREATE VIRTUAL TABLE poi_ix USING rtree(id, minlon, maxlon, minlat, maxlat);
CREATE TABLE land(id INTEGER PRIMARY KEY, osm TEXT UNIQUE, cls TEXT, wkb BLOB);
CREATE VIRTUAL TABLE land_ix USING rtree(id, minlon, maxlon, minlat, maxlat);
CREATE TABLE river(id INTEGER PRIMARY KEY, coords BLOB);
CREATE VIRTUAL TABLE river_ix USING rtree(id, minlon, maxlon, minlat, maxlat);
"""


def build(dst, regions, workdir, keep_downloads=False):
    t0 = time.time()
    tmp = dst + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    poi_seq = land_seq = 0
    for region in regions:
        pbf, poly = download(region, workdir)
        db.execute("INSERT OR REPLACE INTO coverage VALUES (?,?)", (region, wkb.dumps(parse_poly(poly))))
        log.info("Verarbeite %s", pbf)
        n_road = n_land = 0
        idx_file = _index_file()
        if idx_file and os.path.exists(idx_file):
            os.remove(idx_file)
        fp = (osmium.FileProcessor(pbf)
              .with_locations(os.environ.get("OSM_INDEX", "flex_mem"))
              .with_areas(osmium.filter.KeyFilter("landuse", "natural", "tourism", "historic",
                                                   "man_made", "amenity")))
        for o in fp:
            if o.is_node():
                if not o.tags:
                    continue
                tg = dict(o.tags)
                loc = o.location
                if tg.get("highway") in CTRL_HW or tg.get("railway") == "level_crossing" or (
                        tg.get("highway") == "crossing" and tg.get("crossing") == "traffic_signals") or (
                        tg.get("barrier") == "height_restrictor" or any(k in tg for k in RESTRICT_NODE)):
                    db.execute("INSERT OR REPLACE INTO ctrl VALUES (?,?,?,?)",
                               (o.id, loc.lat, loc.lon, json.dumps(_sub(tg, CTRL_TAGS))))
                    db.execute("INSERT OR REPLACE INTO ctrl_ix VALUES (?,?,?,?,?)",
                               (o.id, loc.lon, loc.lon, loc.lat, loc.lat))
                if _is_poi(tg):
                    poi_seq += 1
                    if db.execute("INSERT OR IGNORE INTO poi VALUES (?,?,?,?,?)",
                                  (poi_seq, f"n{o.id}", loc.lat, loc.lon,
                                   json.dumps(_sub(tg, POI_TAGS)))).rowcount:
                        db.execute("INSERT INTO poi_ix VALUES (?,?,?,?,?)", (poi_seq, loc.lon, loc.lon, loc.lat, loc.lat))
            elif o.is_way():
                tg = dict(o.tags) if o.tags else {}
                hw = tg.get("highway")
                if (hw and hw not in ROAD_EXCLUDE and tg.get("area") != "yes" and hw != "track"
                        and not (hw == "service" and tg.get("service") in SKIP_SERVICE)):
                    ids, co = array("q"), array("i")
                    ok = True
                    for n in o.nodes:
                        if not n.location.valid():
                            ok = False
                            break
                        ids.append(n.ref)
                        co.extend((round(n.location.lat * COORD), round(n.location.lon * COORD)))
                    if ok and len(ids) >= 2:
                        lats, lons = [v / COORD for v in co[0::2]], [v / COORD for v in co[1::2]]
                        db.execute("INSERT OR REPLACE INTO road VALUES (?,?,?,?)",
                                   (o.id, json.dumps(_sub(tg, ROAD_TAGS)), ids.tobytes(), co.tobytes()))
                        db.execute("INSERT OR REPLACE INTO road_ix VALUES (?,?,?,?,?)",
                                   (o.id, min(lons), max(lons), min(lats), max(lats)))
                        n_road += 1
                elif hw in ("services", "rest_area") and _is_poi(tg):   # Raststaette als Linie/Flaeche
                    pts = [(n.location.lat, n.location.lon) for n in o.nodes if n.location.valid()]
                    if pts:
                        la = sum(p[0] for p in pts) / len(pts)
                        lo = sum(p[1] for p in pts) / len(pts)
                        poi_seq += 1
                        if db.execute("INSERT OR IGNORE INTO poi VALUES (?,?,?,?,?)",
                                      (poi_seq, f"w{o.id}", la, lo, json.dumps(_sub(tg, POI_TAGS)))).rowcount:
                            db.execute("INSERT INTO poi_ix VALUES (?,?,?,?,?)", (poi_seq, lo, lo, la, la))
                if tg.get("waterway") in ("river", "canal"):
                    co = array("i")
                    for n in o.nodes:
                        if n.location.valid():
                            co.extend((round(n.location.lat * COORD), round(n.location.lon * COORD)))
                    if len(co) >= 4:
                        db.execute("INSERT OR REPLACE INTO river VALUES (?,?)", (o.id, co.tobytes()))
                        db.execute("INSERT OR REPLACE INTO river_ix VALUES (?,?,?,?,?)",
                                   (o.id, min(co[1::2]) / COORD, max(co[1::2]) / COORD, min(co[0::2]) / COORD, max(co[0::2]) / COORD))
            elif o.is_area():
                tg = dict(o.tags)
                key = f"{'w' if o.from_way() else 'r'}{o.orig_id()}"
                cls = _land_class(tg)
                poi = _is_poi(tg)
                if not cls and not poi:
                    continue
                try:
                    parts = []
                    for outer in o.outer_rings():
                        shell = [(n.lon, n.lat) for n in outer]
                        holes = [[(n.lon, n.lat) for n in inner] for inner in o.inner_rings(outer)]
                        parts.append(Polygon(shell, holes))
                    geom = parts[0] if len(parts) == 1 else MultiPolygon(parts)
                except Exception:  # noqa: BLE001 – kaputte Geometrie ueberspringen, Bau laeuft weiter
                    continue
                if geom.is_empty:
                    continue
                if poi:
                    c = geom.representative_point()
                    poi_seq += 1
                    if db.execute("INSERT OR IGNORE INTO poi VALUES (?,?,?,?,?)",
                                  (poi_seq, key, c.y, c.x, json.dumps(_sub(tg, POI_TAGS)))).rowcount:
                        db.execute("INSERT INTO poi_ix VALUES (?,?,?,?,?)", (poi_seq, c.x, c.x, c.y, c.y))
                if cls and (geom.area >= MIN_AREA or cls == "tourism=camp"):
                    # Camp-Flaechen nicht vereinfachen (Grenze entscheidet „oeffentlich“ oder „nur Gaeste“)
                    g = geom if cls == "tourism=camp" else geom.simplify(SIMPLIFY, preserve_topology=True)
                    if g.is_empty:
                        continue
                    land_seq += 1
                    if db.execute("INSERT OR IGNORE INTO land VALUES (?,?,?,?)",
                                  (land_seq, key, cls, wkb.dumps(g))).rowcount:
                        x0, y0, x1, y1 = g.bounds
                        db.execute("INSERT INTO land_ix VALUES (?,?,?,?,?)", (land_seq, x0, x1, y0, y1))
                        n_land += 1
        db.commit()
        log.info("%s: %d Strassen, %d Flaechen (%.0f s)", region, n_road, n_land, time.time() - t0)
        if not keep_downloads:
            os.remove(pbf)
        if idx_file and os.path.exists(idx_file):
            os.remove(idx_file)
    db.execute("INSERT INTO meta VALUES ('built', ?)", (time.strftime("%Y-%m-%dT%H:%M:%S"),))
    db.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
    db.execute("INSERT INTO meta VALUES ('regions', ?)", (json.dumps(regions),))
    db.commit()
    db.execute("ANALYZE")
    db.close()
    os.replace(tmp, dst)
    log.info("Fertig: %s (%.0f MB, %.0f s)", dst, os.path.getsize(dst) / 1e6, time.time() - t0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build(sys.argv[1], sys.argv[2:], os.environ.get("OSM_WORKDIR", os.path.dirname(os.path.abspath(sys.argv[1]))),
          keep_downloads=os.environ.get("KEEP_DOWNLOADS") == "1")
