"""Umweltzonen-Daten (seit v0.19.0): Frankreich aus der BNZFE (transport.data.gouv.fr, Licence Ouverte 2.0),
uebriges Europa aus OpenStreetMap (boundary=low_emission_zone, Overpass, ODbL). Zusammengefuehrt in
DATA_DIR/lez/zones.geojson + meta.json. Abruf schlaegt fehl -> letzter guter Stand bleibt, Ereignis „fehler“.
Regeln je Land stehen von Hand gepflegt in lezrules.json (mit Quelle und Pruefdatum)."""

import datetime as dt
import json
import logging
import os
import re
import threading

import httpx

import events
import overpass
import weather

log = logging.getLogger("lez")

DIR = os.path.join(os.environ.get("DATA_DIR", "/data"), "lez")
# Ressource „aires.geojson“ des Datensatzes „Base Nationale des ZFE“ – stabile Weiterleitung auf die jeweils neueste Datei
BNZFE_URL = "https://transport.data.gouv.fr/resources/79567/download"
OSM_QUERY = ('[out:json][timeout:180];(relation["boundary"="low_emission_zone"](34,-11,72,32);'
             'way["boundary"="low_emission_zone"](34,-11,72,32););out geom;')
SIMPLIFY = 0.0002        # Grad, ~20 m
DUP_SHARE = 0.5          # OSM-Flaeche faellt weg, wenn so viel in einer BNZFE-Flaeche liegt
_RULES_FILE = os.path.join(os.path.dirname(__file__), "lezrules.json")
UNKNOWN = {"kind": "unknown"}
_lock = threading.Lock()
_cache = {"key": None, "zones": None}
_rules = None


def _reset_cache():
    _cache.update(key=None, zones=None)


def rules() -> dict:
    """Regeltabelle je Land (lezrules.json), einmal geladen."""
    global _rules
    if _rules is None:
        with open(_RULES_FILE, encoding="utf-8") as f:
            _rules = json.load(f)
    return _rules


def _country_rule(country, zone_id, name) -> dict:
    """Regel aus der Landestabelle: Treffer ueber id oder Namensmuster (Teilstring, ohne Gross/Klein), sonst Standard."""
    entry = rules().get(country)
    if not entry:
        return dict(UNKNOWN)
    for key, rule in entry.get("zones", {}).items():
        if key == zone_id or key.lower() in (name or "").lower():
            return dict(rule)
    return dict(entry.get("default") or UNKNOWN)


# ---------------------------------------------------------------- Frankreich (BNZFE)

def _critair(v):
    """BNZFE „Vn“ ist die erste VERBOTENE Klasse (V4 sperrt 4, 5 und ohne Vignette); gespeichert wird die hoechste
    ERLAUBTE Klasse: Vn -> n-1 (V1 -> 0 = nur Elektro), „EL“ -> 0, „NC“ (nur ohne Vignette gesperrt) -> 5, sonst None."""
    t = str(v or "").strip().upper()
    m = re.fullmatch(r"V([1-5])", t)
    if m:
        return int(m.group(1)) - 1
    return {"NC": 5, "EL": 0}.get(t)


_ALLDAY = re.compile(r"(?:(?:mo|montag)\s*-\s*(?:su|so)\s*:?\s*)?(?:00:00|0:00)\s*-\s*(?:24:00|00:00)", re.I)


def _hours(v):
    """Zeitfenster der Zone; ganztaegig und ganzwoechig („24/7“, „Mo-Su : 00:00-00:00“, „00:00-24:00“) -> None (seit v0.19)."""
    v = (v or "").strip()
    if v == "" or v.lower() == "24/7" or _ALLDAY.fullmatch(v):
        return None
    return v


_IDLIKE = re.compile(r"[\d\s\-_/.]*(?:zfe[\d\s\-_/.]*)?", re.I)
# Traeger ohne publisher-Eintrag in der BNZFE: Name aus der Host-Kennung der Info-Seite (Stand 10/2026)
_HOST_NAMES = {"grandannecy.fr": "Grand Annecy", "grenoblealpesmetropole.fr": "Grenoble-Alpes Métropole",
               "metropole.rennes.fr": "Rennes Métropole", "metropole.nantes.fr": "Nantes Métropole",
               "annemasse-agglo.fr": "Annemasse Agglo"}


def _bnzfe_name(p, pub, zid):
    """Anzeigename: eigener `nom`, sonst Traeger (publisher.nom), sonst Name nach Info-Seite, zuletzt „Umweltzone“.
    Leere Namen und Kennungen („1“, „200040715-ZFE-001“) zaehlen als fehlend (seit v0.19)."""
    for n in (p.get("nom"), pub.get("nom")):
        n = (n or "").strip()
        if n and not _IDLIKE.fullmatch(n):
            return n
    url = p.get("url_site") or p.get("url_site_information") or ""
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
    for h, n in _HOST_NAMES.items():
        if host == h or host.endswith("." + h):
            return n
    return "Umweltzone"


def _date(v, end=False):
    v = (v or "")[:10]
    if not v or (end and v >= "2999"):
        return None
    return v


def parse_bnzfe(geojson: dict) -> list:
    from shapely.geometry import shape
    out = []
    for f in geojson.get("features", []):
        if not f.get("geometry"):
            continue
        p = f.get("properties") or {}
        pub = f.get("publisher") or {}
        zid = p.get("id") or pub.get("zfe_id")
        if not zid:
            continue
        vp, pl = _critair(p.get("vp_critair")), _critair(p.get("pl_critair"))
        if vp is None and pl is None:
            rule = dict(UNKNOWN)
        else:
            rule = {"kind": "critair", "vp": vp, "pl": pl, "vignette": True,
                    "hours": _hours(p.get("vp_horaires") if vp is not None else p.get("pl_horaires"))}
        out.append({"id": f"fr:{zid}", "name": _bnzfe_name(p, pub, zid), "country": "france",
                    "source": "bnzfe", "rule": rule, "since": _date(p.get("date_debut")),
                    "until": _date(p.get("date_fin"), end=True), "geom": _as_multi(shape(f["geometry"]).buffer(0))})
    return out


# ---------------------------------------------------------------- OSM

def _as_multi(g):
    from shapely.geometry import MultiPolygon
    if g.geom_type == "Polygon":
        return MultiPolygon([g])
    if g.geom_type == "MultiPolygon":
        return g
    return MultiPolygon([p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"])


def _poly_of(el):
    """Way (geschlossener Ring) oder Relation (Aussen-/Innenwege zu Ringen verbinden) -> Geometrie oder None."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import linemerge, polygonize, unary_union

    def line(g):
        return LineString([(n["lon"], n["lat"]) for n in g]) if g and len(g) >= 2 else None

    if el["type"] == "way":
        pts = el.get("geometry") or []
        if len(pts) < 4:
            return None
        return _as_multi(Polygon([(n["lon"], n["lat"]) for n in pts]).buffer(0))
    outer, inner = [], []
    for m in el.get("members", []):
        ln = line(m.get("geometry"))
        if ln is not None and m.get("type") == "way":
            (inner if m.get("role") == "inner" else outer).append(ln)
    if not outer:
        return None
    g = unary_union(list(polygonize(linemerge(outer))))
    if g.is_empty:
        return None
    if inner:
        holes = unary_union(list(polygonize(linemerge(inner))))
        if not holes.is_empty:
            g = g.difference(holes)
    return _as_multi(g)


def _osm_rule(tags, country, zid, name):
    if "green_sticker" in str(tags.get("access", "")):
        return {"kind": "sticker", "need": "green"}
    return _country_rule(country, zid, name)


def parse_overpass(data: dict) -> list:
    out = []
    for el in data.get("elements", []):
        if el.get("type") not in ("way", "relation"):
            continue
        g = _poly_of(el)
        if g is None or g.is_empty:
            continue
        tags = el.get("tags") or {}
        c = g.centroid
        country = weather._country_of(c.y, c.x)
        zid = f"osm:{el['type']}/{el['id']}"
        name = tags.get("name") or "Umweltzone"
        out.append({"id": zid, "name": name, "country": country, "source": "osm",
                    "rule": _osm_rule(tags, country, zid, name), "since": _date(tags.get("start_date")),
                    "until": _date(tags.get("end_date")), "geom": g})
    return out


def _fetch_osm() -> dict:
    return overpass.query(OSM_QUERY, timeout=200, require=True)


# ---------------------------------------------------------------- Zusammenfuehren, Speichern

def merge(fr: list, osm: list) -> list:
    """BNZFE gewinnt: OSM-Flaechen, die zu mehr als 50 % in einer BNZFE-Flaeche liegen, fallen weg."""
    from shapely.ops import unary_union
    cover = unary_union([z["geom"] for z in fr]) if fr else None
    keep = []
    for z in osm:
        if cover is not None and z["geom"].area > 0 and \
                z["geom"].intersection(cover).area / z["geom"].area > DUP_SHARE:
            continue
        keep.append(z)
    return list(fr) + keep


def _write(path, obj):
    os.makedirs(DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _save(zs):
    from shapely.geometry import mapping
    feats = [{"type": "Feature",
              "properties": {k: z[k] for k in ("id", "name", "country", "source", "rule", "since", "until")},
              "geometry": mapping(z["geom"].simplify(SIMPLIFY, preserve_topology=True))} for z in zs]
    _write(os.path.join(DIR, "zones.geojson"), {"type": "FeatureCollection", "features": feats})


def _load():
    from shapely.geometry import shape
    path = os.path.join(DIR, "zones.geojson")
    try:
        mt = os.path.getmtime(path)
        if _cache["key"] == mt:
            return _cache["zones"]
        with open(path, encoding="utf-8") as f:
            fc = json.load(f)
        zs = [{**f["properties"], "geom": _as_multi(shape(f["geometry"]))} for f in fc["features"]]
    except Exception:   # noqa: BLE001 – fehlende/kaputte Datei oder Geometrie: lieber „kein Stand“ als Absturz
        return None
    _cache.update(key=mt, zones=zs)
    return zs


def zones():
    """Alle Zonen (gecacht, aendert sich mit der Datei) oder None = kein Datenstand."""
    return _load()


ROUTE_MIN_KM = 0.2       # weniger Routenanteil in einer Zone zaehlt nicht
POLY_TOL = 0.0005        # Grad, ~50 m: Umriss fuer Karte/Paket/Umfahren


def _rings(geom):
    """Vereinfachte Aussenringe als [[lat, lon], ...]."""
    out = []
    for p in _as_multi(geom.simplify(POLY_TOL, preserve_topology=True)).geoms:
        out.append([[round(y, 5), round(x, 5)] for x, y in p.exterior.coords])
    return out


def on_route(plat, plon, km):
    """Zonen, die die Route mit mindestens 200 m schneidet (seit v0.19.0, nur Rig). Je Zonen-Eintrag (auch die
    zeitlichen BNZFE-Stufen) ein Ergebnis mit km0/km1 entlang `km`, nach km0 sortiert; None = kein Datenstand."""
    import numpy as np
    from shapely.geometry import LineString, Point
    zs = zones()
    if zs is None:
        return None
    lat, lon, km = (np.asarray(a, dtype=float) for a in (plat, plon, km))
    if len(lat) < 2:
        return []
    line = LineString(np.column_stack([lon, lat]))
    cum = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(lon), np.diff(lat)))])   # Grad-Laenge je Stuetzpunkt

    def km_at(pt):
        return float(np.interp(line.project(Point(pt)), cum, km))

    out = []
    for z in zs:
        if not line.intersects(z["geom"]):
            continue
        g = line.intersection(z["geom"])
        parts = [x for x in getattr(g, "geoms", [g]) if x.geom_type == "LineString" and not x.is_empty]
        spans = []
        for x in parts:
            a, b = km_at(x.coords[0]), km_at(x.coords[-1])
            spans.append((min(a, b), max(a, b)))
        if not spans or sum(b - a for a, b in spans) < ROUTE_MIN_KM:
            continue
        out.append({"id": z["id"], "name": z["name"], "country": z["country"], "source": z["source"],
                    "km0": round(min(a for a, _ in spans), 2), "km1": round(max(b for _, b in spans), 2),
                    "rule": z["rule"], "since": z["since"], "until": z["until"], "poly": _rings(z["geom"])})
    out.sort(key=lambda e: e["km0"])
    return out


def meta():
    try:
        with open(os.path.join(DIR, "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


REFRESH_DAYS = 7


def due(m, now=None) -> bool:
    """Neuabruf faellig: kein Stand, eine Quelle nie geklappt oder die aeltere laenger als 7 Tage her."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        stamps = [dt.datetime.fromisoformat(m[k]) for k in ("bnzfe", "osm")]
    except (TypeError, KeyError, ValueError):
        return True
    stamps = [s if s.tzinfo else s.replace(tzinfo=dt.timezone.utc) for s in stamps]
    return now - min(stamps) > dt.timedelta(days=REFRESH_DAYS)


def refresh(now=None) -> dict:
    """Beide Quellen getrennt holen; Fehlschlag haelt den alten Stand der Quelle. Gibt die Meta zurueck."""
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    with _lock:
        old = _load() or []
        old_meta = meta() or {}
        fr = [z for z in old if z["source"] == "bnzfe"]
        osm = [z for z in old if z["source"] == "osm"]
        m = {"bnzfe": old_meta.get("bnzfe"), "osm": old_meta.get("osm"), "errors": {}}

        def run(name, fetch, parse):
            try:
                return parse(fetch()), True
            except Exception as e:   # noqa: BLE001 – Netz, Overpass, kaputtes JSON: Stand bleibt, Tagebuch bekommt „fehler“
                log.info("Umweltzonen %s: %s", name, e)   # info: das „fehler“-Ereignis schreiben wir selbst (kein Doppel ueber ErrorHandler)
                m["errors"][name] = f"{e.__class__.__name__}: {str(e)[:200]}"
                events.log("fehler", now=now.replace(tzinfo=None), quelle=f"lez-{name}", meldung=m["errors"][name])
                return None, False

        def get_bnzfe():
            r = httpx.get(BNZFE_URL, headers={"User-Agent": overpass.USER_AGENT}, timeout=90, follow_redirects=True)
            r.raise_for_status()
            return r.json()

        new, ok = run("bnzfe", get_bnzfe, parse_bnzfe)
        if ok:
            fr = [z for z in new if not z["until"] or z["until"] >= today]
            m["bnzfe"] = stamp
        new, ok = run("osm", _fetch_osm, parse_overpass)
        if ok:
            osm = [z for z in new if not z["until"] or z["until"] >= today]
            m["osm"] = stamp
        zs = merge(fr, osm)
        if m["bnzfe"] and m["osm"] and zs:   # erst veroeffentlichen, wenn beide Quellen je einmal geklappt haben
            _save(zs)
            m["count"] = len(zs)
        else:
            m["count"] = 0
        _write(os.path.join(DIR, "meta.json"), m)
        _reset_cache()
    return m


MAX_EXCLUDE = 20         # Valhalla: hoechstens so viele Ausschlusszonen je Anfrage


def exclude_polygons(ids, keep_points):
    """Zonen -> Valhalla `exclude_polygons` (seit v0.19.0, nur Rig): ([[ [lon, lat], ... ], ...], ids_nicht_ausgeschlossen).
    keep_points = [(lat, lon), ...] (Start/Ziel): eine Zone, die einen davon enthaelt, wird nicht ausgeschlossen
    (sonst gaebe es keine Route) und steht in der zweiten Liste. Unbekannte IDs fallen weg, hoechstens 20 Zonen;
    MultiPolygon = ein Ring je Teil, zaehlt einmal. Kein Datenstand -> ([], [])."""
    from shapely.geometry import Point
    zs = zones()
    if not zs or not ids:
        return [], []
    by_id = {z["id"]: z for z in zs}
    keeps = [Point(lon, lat) for lat, lon in keep_points or []]
    polys, kept, n = [], [], 0
    for zid in dict.fromkeys(i for i in ids if isinstance(i, str)):
        z = by_id.get(zid)
        if z is None:
            continue
        if any(z["geom"].intersects(k) for k in keeps):
            kept.append(zid)
            continue
        if n >= MAX_EXCLUDE:
            break
        n += 1
        polys += [[[lon, lat] for lat, lon in ring] for ring in _rings(z["geom"])]
    return polys, kept
