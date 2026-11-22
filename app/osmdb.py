"""Lesezugriff auf die lokale OSM-Datenbank (siehe build_db.py).

Liefert dieselben Strukturen wie die Overpass-Abfragen in analyze.py, damit die
Analyse je Teilstueck zwischen lokaler DB und Overpass waehlen kann.
"""

import json
import logging
import os
import sqlite3
import threading
from array import array

import numpy as np
import shapely
from shapely import wkb
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

log = logging.getLogger("osmdb")

DEG_LAT = 110574.0


def poi_radius(tg):
    """Wie weit abseits der Route ein POI noch zaehlt (m)."""
    if "place" in tg:
        return 1500
    if tg.get("tourism") in ("caravan_site", "camp_site") or tg.get("amenity") in ("sanitary_dump_station", "water_point"):
        return 3000    # Stellplatz/Entsorgung: ein kleiner Umweg ist ok
    if tg.get("amenity") == "fuel" or tg.get("highway") in ("services", "rest_area"):
        return 500
    if tg.get("amenity"):
        return 250
    return 800


# Namensvorschlag fuer per Klick gesetzte Planer-Punkte – lokal aus place=* der Regionen-DB statt
# Rueckwaertssuche bei Nominatim. Je Ortsart: bis wohin „im Ort“ (Name) und wie weit hoechstens noch „bei <Ort>“ (m).
PLACE_RANGE = {"city": (4000, 15000), "town": (2000, 8000), "suburb": (800, 2500), "village": (800, 3000),
               "hamlet": (400, 1500)}


def pick_place(cands):
    """cands: [(name, art, dist_m)] -> „Wetzlar“ (im Ort), „bei Gießen“ (in der Naehe) oder "" (nichts Passendes).
    Groessere Orte duerfen weiter weg sein: verglichen wird die Entfernung relativ zur Reichweite der Ortsart."""
    best = None
    for name, kind, dist in cands:
        rng = PLACE_RANGE.get(kind)
        if not name or not rng or dist > rng[1]:
            continue
        score = dist / rng[1]
        if best is None or score < best[0]:
            best = (score, name, dist <= rng[0])
    if best is None:
        return ""
    return best[1] if best[2] else f"bei {best[1]}"


def _dist_m(lat1, lon1, lat2, lon2):
    dy = (lat2 - lat1) * DEG_LAT
    dx = (lon2 - lon1) * DEG_LAT * np.cos(np.radians((lat1 + lat2) / 2))
    return float(np.hypot(dx, dy))


class OsmDB:
    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        db = self._db()
        self.meta = dict(db.execute("SELECT k, v FROM meta"))
        self.regions = json.loads(self.meta.get("regions", "[]"))
        # ab Schema 3 Koordinaten als int32 (1e-7 Grad), davor float64
        self.i32 = str(self.meta.get("schema", "1")).isdigit() and int(self.meta.get("schema", "1")) >= 3
        self.coverage = unary_union([wkb.loads(r[0]) for r in db.execute("SELECT wkb FROM coverage")])
        # 300 m Sicherheitsabstand zum Rand: dort fehlen Querstrassen/POIs jenseits der Grenze
        self.inner = self.coverage.buffer(-0.004)
        shapely.prepare(self.inner)

    def _db(self):
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            self._local.db = db
        return db

    def in_camp(self, lat, lon):
        """Liegt der Punkt auf dem Gelaende eines Camping-/Stellplatzes (Flaechen ab Schema 4)?"""
        from shapely.geometry import Point
        ids = [r[0] for r in self._db().execute(
            "SELECT id FROM land_ix WHERE minlon<=? AND maxlon>=? AND minlat<=? AND maxlat>=?", (lon, lon, lat, lat))]
        if not ids:
            return False
        pt = Point(lon, lat)
        return any(wkb.loads(g).contains(pt) for cls, g in self._rows("SELECT cls, wkb FROM land WHERE id IN ({})", ids)
                   if cls == "tourism=camp")

    def covers(self, lat, lon):
        """True, wenn alle Punkte im Abdeckungsgebiet (mit Randabstand) liegen."""
        return bool(np.all(shapely.contains_xy(self.inner, np.asarray(lon), np.asarray(lat))))

    def _coords(self, blob):
        if self.i32:
            return np.frombuffer(blob, dtype=np.int32).reshape(-1, 2) / 1e7
        return np.frombuffer(blob, dtype=np.float64).reshape(-1, 2)

    # ------------------------------------------------------------------ Hilfen
    @staticmethod
    def _boxes(lat, lon, margin_m, every=100):
        """Kleine Suchboxen entlang der Strecke (alle ~1 km) statt einer grossen."""
        mlat = margin_m / DEG_LAT
        mlon = margin_m / (DEG_LAT * np.cos(np.radians(np.mean(lat))))
        out = []
        for a in range(0, len(lat), every):
            b = min(a + every + 1, len(lat))
            la, lo = lat[a:b], lon[a:b]
            out.append((lo.min() - mlon, lo.max() + mlon, la.min() - mlat, la.max() + mlat))
        return out

    def _ids(self, table, boxes):
        db = self._db()
        ids = set()
        q = f"SELECT id FROM {table}_ix WHERE maxlon>=? AND minlon<=? AND maxlat>=? AND minlat<=?"  # noqa: S608 – table nur aus dem Code
        for x0, x1, y0, y1 in boxes:
            ids.update(r[0] for r in db.execute(q, (x0, x1, y0, y1)))
        return ids

    def _rows(self, sql, ids):
        db = self._db()
        ids = list(ids)
        out = []
        for k in range(0, len(ids), 900):
            part = ids[k:k + 900]
            out += db.execute(sql.format(",".join("?" * len(part))), part).fetchall()
        return out

    # ------------------------------------------------------------------ Abfragen
    def roads_near(self, lat, lon, proj, radius, classes=None):
        """Nur Strassen (ohne POIs/Flaechen) bis radius Meter um die Linie, wahlweise nur bestimmte highway-Klassen –
        fuer das Schliessen grosser Aufzeichnungsluecken (seit v0.13.2)."""
        lat, lon = np.asarray(lat), np.asarray(lon)
        x, y = proj.fwd(lat, lon)
        line = LineString(np.column_stack([x, y])) if len(lat) > 1 else Point(float(x[0]), float(y[0]))
        ids = self._ids("road", self._boxes(lat, lon, radius + 10))
        out = []
        for wid, tags, nodes, coords in self._rows("SELECT id, tags, nodes, coords FROM road WHERE id IN ({})", ids):
            tg = json.loads(tags)
            if classes is not None and tg.get("highway") not in classes:
                continue
            co = self._coords(coords)
            gx, gy = proj.fwd(co[:, 0], co[:, 1])
            if not shapely.dwithin(LineString(np.column_stack([gx, gy])), line, radius):
                continue
            nl = array("q")
            nl.frombytes(nodes)
            out.append({"type": "way", "id": wid, "tags": tg, "nodes": nl.tolist(),
                        "geometry": [{"lat": a, "lon": b} for a, b in co.tolist()]})
        return out

    def fetch(self, lat, lon, proj, match_radius=25.0):
        """lat/lon: Rasterpunkte des Teilstuecks. Gibt (roads, pois, land, rivers) zurueck:
        roads/pois als Overpass-artige Elemente, land als [(geom_lonlat, 'key=value')],
        rivers als [np.array lat/lon]."""
        lat, lon = np.asarray(lat), np.asarray(lon)
        x, y = proj.fwd(lat, lon)
        line = LineString(np.column_stack([x, y]))
        shapely.prepare(line)

        ids = self._ids("road", self._boxes(lat, lon, match_radius + 10))
        rows = self._rows("SELECT id, tags, nodes, coords FROM road WHERE id IN ({})", ids)
        ways, geoms = [], []
        for wid, tags, nodes, coords in rows:
            co = self._coords(coords)
            gx, gy = proj.fwd(co[:, 0], co[:, 1])
            geoms.append(LineString(np.column_stack([gx, gy])))
            ways.append((wid, tags, nodes, co))
        keep = shapely.dwithin(np.array(geoms, dtype=object), line, match_radius) if geoms else []
        roads, node_ids = [], set()
        for (wid, tags, nodes, co), k in zip(ways, keep):
            if not k:
                continue
            nl = array("q")
            nl.frombytes(nodes)
            nl = nl.tolist()
            node_ids.update(nl)
            roads.append({"type": "way", "id": wid, "tags": json.loads(tags), "nodes": nl,
                          "geometry": [{"lat": a, "lon": b} for a, b in co.tolist()]})
        cids = self._ids("ctrl", self._boxes(lat, lon, match_radius + 10)) & node_ids
        for nid, la, lo, tags in self._rows("SELECT id, lat, lon, tags FROM ctrl WHERE id IN ({})", cids):
            roads.append({"type": "node", "id": nid, "lat": la, "lon": lo, "tags": json.loads(tags)})

        # POIs + Orte (Radius je Art wie in der Overpass-Abfrage)
        pids = self._ids("poi", self._boxes(lat, lon, 3000))
        pois = []
        prow = self._rows("SELECT osm, lat, lon, tags FROM poi WHERE id IN ({})", pids)
        if prow:
            px, py = proj.fwd([r[1] for r in prow], [r[2] for r in prow])
            d = shapely.distance(shapely.points(np.column_stack([px, py])), line)
            for (osm, la, lo, tags), dist in zip(prow, d):
                tg = json.loads(tags)
                lim = poi_radius(tg)
                if dist <= lim:
                    typ = {"n": "node", "w": "way", "r": "relation"}[osm[0]]
                    el = {"type": typ, "id": int(osm[1:]), "tags": tg}
                    if typ == "node":
                        el.update(lat=la, lon=lo)
                    else:
                        el["center"] = {"lat": la, "lon": lo}
                    pois.append(el)

        lids = self._ids("land", self._boxes(lat, lon, 80))
        land = [(wkb.loads(g), cls) for cls, g in self._rows("SELECT cls, wkb FROM land WHERE id IN ({})", lids)]
        rids = self._ids("river", self._boxes(lat, lon, 300))
        rivers = [self._coords(c)
                  for (c,) in self._rows("SELECT coords FROM river WHERE id IN ({})", rids)]
        return roads, pois, land, rivers

    def fuel_near(self, lat, lon, proj, radius):
        """Tankstellen bis radius (m) um einzelne Streckenpunkte (z. B. Autobahn-Abschnitte) -> [(el, dist_m)].
        Filter auf amenity=fuel schon im SQL, damit nicht alle POIs im 5-km-Streifen geparst werden."""
        lat, lon = np.asarray(lat), np.asarray(lon)
        if not len(lat):
            return []
        ids = self._ids("poi", self._boxes(lat, lon, radius, every=20))
        rows = self._rows("SELECT osm, lat, lon, tags FROM poi WHERE id IN ({}) AND tags LIKE '%\"amenity\": \"fuel\"%'", ids)
        if not rows:
            return []
        x, y = proj.fwd(lat, lon)
        pts = shapely.multipoints(np.column_stack([x, y]))
        px, py = proj.fwd([r[1] for r in rows], [r[2] for r in rows])
        d = shapely.distance(shapely.points(np.column_stack([px, py])), pts)
        out = []
        for (osm, la, lo, tags), dist in zip(rows, d):
            if dist <= radius:
                typ = {"n": "node", "w": "way", "r": "relation"}[osm[0]]
                el = {"type": typ, "id": int(osm[1:]), "tags": json.loads(tags)}
                el.update({"lat": la, "lon": lo} if typ == "node" else {"center": {"lat": la, "lon": lo}})
                out.append((el, float(dist)))
        return out


    def camps_near(self, lat, lon, proj, radius):
        """Stellplaetze und Campingplaetze (tourism=caravan_site/camp_site) bis radius (m) um die Streckenpunkte
        -> [(el, dist_m)]. Fuer die Nachtplatz-Vorschlaege des Planers (seit v0.21.0)."""
        lat, lon = np.asarray(lat), np.asarray(lon)
        if not len(lat):
            return []
        ids = self._ids("poi", self._boxes(lat, lon, radius, every=20))
        rows = self._rows("SELECT osm, lat, lon, tags FROM poi WHERE id IN ({}) AND (tags LIKE '%\"tourism\": \"caravan_site\"%'"
                          " OR tags LIKE '%\"tourism\": \"camp_site\"%')", ids)
        if not rows:
            return []
        x, y = proj.fwd(lat, lon)
        line = shapely.linestrings(np.column_stack([x, y])) if len(x) > 1 else shapely.points(x[0], y[0])
        px, py = proj.fwd([r[1] for r in rows], [r[2] for r in rows])
        d = shapely.distance(shapely.points(np.column_stack([px, py])), line)
        out = []
        for (osm, la, lo, tags), dist in zip(rows, d):
            if dist <= radius:
                typ = {"n": "node", "w": "way", "r": "relation"}[osm[0]]
                el = {"type": typ, "id": int(osm[1:]), "tags": json.loads(tags)}
                el.update({"lat": la, "lon": lo} if typ == "node" else {"center": {"lat": la, "lon": lo}})
                out.append((el, float(dist)))
        return out

    def places_near(self, lat, lon, radius):
        """Benannte Orte (place=city/town/village/hamlet/suburb) bis radius (m) -> [(name, art, dist_m)]."""
        mlat = radius / DEG_LAT
        mlon = radius / (DEG_LAT * np.cos(np.radians(lat)))
        ids = self._ids("poi", [(lon - mlon, lon + mlon, lat - mlat, lat + mlat)])
        out = []
        for la, lo, tags in self._rows("SELECT lat, lon, tags FROM poi WHERE id IN ({}) AND tags LIKE '%\"place\": %'", ids):
            tg = json.loads(tags)
            if tg.get("place") in PLACE_RANGE and tg.get("name"):
                out.append((tg["name"], tg["place"], _dist_m(lat, lon, la, lo)))
        return out

    def ramps_near(self, lat, lon, proj, radius=300.0):
        """Punkte von Autobahn-Auf-/Abfahrten (motorway_link) nahe der Streckenpunkte -> np.array lat/lon.
        Grundlage fuer den Abstecher zur Tankstelle: gemessen wird ab der Ausfahrt, nicht ab der Autobahn."""
        lat, lon = np.asarray(lat), np.asarray(lon)
        if not len(lat):
            return np.zeros((0, 2))
        ids = self._ids("road", self._boxes(lat, lon, radius, every=20))
        rows = self._rows("SELECT coords FROM road WHERE id IN ({}) AND tags LIKE '%\"highway\": \"motorway_link\"%'", ids)
        co = [self._coords(c) for (c,) in rows]
        return np.vstack(co) if co else np.zeros((0, 2))


class OsmDBSet:
    """Mehrere DB-Dateien (je Region eine, dazu ggf. die alte Sammel-DB) wie eine.
    Teilstuecke an Grenzen (Kaernten/Slowenien) holen aus allen beteiligten DBs."""

    def __init__(self, dbs):
        self.dbs = dbs
        self.regions = sorted({r for d in dbs for r in d.regions})
        built = [d.meta.get("built", "") for d in dbs if d.meta.get("built")]
        self.meta = {"built": min(built) if built else ""}
        # erst vereinigen, dann den Randabstand: sonst bleibt an der Grenze zweier lokaler
        # Regionen (z. B. Oberbayern/Oesterreich) ein Streifen "ohne Daten" -> unnoetig Overpass
        self.inner = unary_union([d.coverage for d in dbs]).buffer(-0.004)
        shapely.prepare(self.inner)

    def covers(self, lat, lon):
        return bool(np.all(shapely.contains_xy(self.inner, np.asarray(lon), np.asarray(lat))))

    def in_camp(self, lat, lon):
        """True/False aus der Region, die den Punkt abdeckt; None = keine Region oder altes Schema (< 4)."""
        from shapely.geometry import Point
        pt = Point(lon, lat)
        for d in self.dbs:
            if d.coverage.contains(pt):
                if int(d.meta.get("schema", "1")) < 4:
                    return None
                return d.in_camp(lat, lon)
        return None

    def places_near(self, lat, lon, radius):
        """Benannte Orte aus allen Regionen, die den Punkt abdecken -> [(name, art, dist_m)]."""
        pt = shapely.Point(lon, lat)
        return [c for d in self.dbs if d.coverage.contains(pt) for c in d.places_near(lat, lon, radius)]

    def place_name(self, lat, lon):
        """Namensvorschlag fuer einen Punkt aus den Regionen, die ihn abdecken ("" = nichts gefunden)."""
        return pick_place(self.places_near(lat, lon, 15000))

    def ramps_near(self, lat, lon, proj, radius=300.0):
        lat, lon = np.asarray(lat), np.asarray(lon)
        if not len(lat):
            return np.zeros((0, 2))
        box = shapely.box(lon.min() - 0.01, lat.min() - 0.01, lon.max() + 0.01, lat.max() + 0.01)
        parts = [d.ramps_near(lat, lon, proj, radius) for d in self.dbs if d.coverage.intersects(box)]
        parts = [p for p in parts if len(p)]
        return np.unique(np.vstack(parts), axis=0) if parts else np.zeros((0, 2))

    def fuel_near(self, lat, lon, proj, radius):
        lat, lon = np.asarray(lat), np.asarray(lon)
        if not len(lat):
            return []
        box = shapely.box(lon.min() - 0.08, lat.min() - 0.06, lon.max() + 0.08, lat.max() + 0.06)
        seen = {}
        for d in self.dbs:
            if d.coverage.intersects(box):
                for el, dist in d.fuel_near(lat, lon, proj, radius):
                    seen[(el["type"], el["id"])] = (el, dist)
        return list(seen.values())

    def camps_near(self, lat, lon, proj, radius):
        lat, lon = np.asarray(lat), np.asarray(lon)
        if not len(lat):
            return []
        pad = radius / 80000 + 0.02
        box = shapely.box(lon.min() - pad, lat.min() - pad, lon.max() + pad, lat.max() + pad)
        seen = {}
        for d in self.dbs:
            if d.coverage.intersects(box):
                for el, dist in d.camps_near(lat, lon, proj, radius):
                    seen[(el["type"], el["id"])] = (el, dist)
        return list(seen.values())

    def roads_near(self, lat, lon, proj, radius, classes=None):
        lat, lon = np.asarray(lat), np.asarray(lon)
        pad = radius / 80000 + 0.02
        box = shapely.box(lon.min() - pad, lat.min() - pad, lon.max() + pad, lat.max() + pad)
        out = {}
        for d in self.dbs:
            if d.coverage.intersects(box):
                for el in d.roads_near(lat, lon, proj, radius, classes):
                    out[el["id"]] = el
        return list(out.values())

    def fetch(self, lat, lon, proj, match_radius=25.0):
        lat, lon = np.asarray(lat), np.asarray(lon)
        box = shapely.box(lon.min() - 0.02, lat.min() - 0.02, lon.max() + 0.02, lat.max() + 0.02)
        roads, pois, land, rivers = {}, {}, [], []
        for d in self.dbs:
            if not d.coverage.intersects(box):
                continue
            r_el, p_el, l_it, rv = d.fetch(lat, lon, proj, match_radius)
            for el in r_el:
                roads[(el["type"], el["id"])] = el
            for el in p_el:
                pois[(el["type"], el["id"])] = el
            land += l_it
            rivers += rv
        return list(roads.values()), list(pois.values()), land, rivers


def db_files(legacy=None, region_dir=None):
    """Vorhandene DB-Dateien: Regionsdateien in OSM_DIR + alte Sammel-DB (OSM_DB)."""
    legacy = legacy or os.environ.get("OSM_DB", "/data/osm.sqlite")
    region_dir = region_dir or os.environ.get("OSM_DIR", "/data/osm")
    files = []
    try:
        names = sorted(f for f in os.listdir(region_dir) if f.endswith(".sqlite"))
        for f in names:
            if f == "shops-de.sqlite":   # Laden-Datensatz (andere Tabellen) gehoert nicht in den Kartenordner
                log.warning("OSM-Ordner: %s ist keine Kartenregion, ignoriert", f)
        files = [os.path.join(region_dir, f) for f in names if f != "shops-de.sqlite"]
    except OSError:
        pass
    if os.path.isfile(legacy):
        files.append(legacy)
    return files


_db = None
_db_key = None
_lock = threading.Lock()


def get():
    """Gemeinsame Instanz ueber alle DB-Dateien; laedt neu, sobald eine Datei
    dazukommt oder ersetzt wird (fertige Region, Monats-Update)."""
    global _db, _db_key
    files = db_files()
    key = tuple((f, os.path.getmtime(f)) for f in files if os.path.exists(f))
    with _lock:
        if key != _db_key:
            dbs = []
            for f, _ in key:
                try:
                    dbs.append(OsmDB(f))
                except Exception as e:  # noqa: BLE001 – eine kaputte Region darf die anderen nicht verhindern
                    log.error("OSM-DB %s unbrauchbar: %s", f, e)
            _db = OsmDBSet(dbs) if dbs else None
            _db_key = key
            if _db:
                log.info("OSM-Daten geladen: %s, Stand %s", _db.regions, _db.meta.get("built"))
        return _db
