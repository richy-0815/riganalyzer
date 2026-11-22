"""Baut den Laden-Datensatz (Deutschland) fuer „Einkaufen unterwegs“ (Rig).

Aufruf:  python shops_build.py <deutschland.osm.pbf> <shops-de.sqlite>
Nimmt shop=supermarket|bakery|butcher|farm (Knoten und Flaechen), Flaechen als Mittelpunkt.
"""

import datetime
import json
import logging
import os
import sqlite3
import sys

import osmium
from shapely.geometry import MultiPolygon, Polygon

log = logging.getLogger("shops_build")

# Marke/Name, ohne Gross-/Kleinschreibung, Praefix-Treffer
DISCOUNT_SHOPS = ("Aldi", "Aldi Nord", "Aldi Süd", "Lidl", "Netto", "Netto Marken-Discount", "Penny", "Norma")
_DIRECT = ("bakery", "butcher", "farm")
# Service unterwegs („Unterwegs entdecken“): shop=* -> eigene Art (Gruppe „service“ in shops.py)
SERVICE = {"car_repair": "repair", "truck_repair": "truck_repair", "caravan": "caravan", "gas": "gas", "laundry": "laundry"}
_DISCOUNT_LC = tuple(s.lower() for s in DISCOUNT_SHOPS)

SCHEMA = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE shop(id INTEGER PRIMARY KEY, osm TEXT, lat REAL, lon REAL, kind TEXT, name TEXT, brand TEXT, hours TEXT, addr TEXT,
                  extra TEXT);
CREATE VIRTUAL TABLE shop_ix USING rtree(id, minlon, maxlon, minlat, maxlat);
"""


def classify(tags):
    """Ladenart (discount/supermarket/bakery/butcher/farm, seit 0.47.0 auch Service: repair/truck_repair/caravan/gas/
    laundry) oder None. Waschsalon nur zum Selberwaschen (keine reine Reinigung mit self_service=no)."""
    shop = tags.get("shop")
    if shop in _DIRECT:
        return shop
    if shop in SERVICE:
        return None if shop == "laundry" and tags.get("self_service") == "no" else SERVICE[shop]
    if shop != "supermarket":
        return None
    label = (tags.get("brand") or tags.get("name") or "").strip().lower()
    if label and label.startswith(_DISCOUNT_LC):
        return "discount"
    return "supermarket"


def _addr(tags):
    street = " ".join(p for p in (tags.get("addr:street"), tags.get("addr:housenumber")) if p)
    town = " ".join(p for p in (tags.get("addr:postcode"), tags.get("addr:city")) if p)
    return ", ".join(p for p in (street, town) if p)


def extra(tg):
    """Detail-Karte (0.54.0, #31): Kontakt, Beschreibung, Betreiber und Leistungen (service:vehicle:*=yes) als JSON –
    nur gesetzte Felder, damit der Datensatz klein bleibt."""
    sv = sorted(k.split(":", 2)[2] for k, v in tg.items() if k.startswith("service:vehicle:") and v in ("yes", "designated"))
    d = {"phone": tg.get("phone") or tg.get("contact:phone") or "", "website": tg.get("website") or tg.get("contact:website") or "",
         "email": tg.get("email") or tg.get("contact:email") or "", "description": (tg.get("description:de") or tg.get("description") or "")[:500],
         "operator": tg.get("operator", ""), "services": sv,
         "gas_exchange": tg.get("shop") == "gas" or tg.get("service:gas") == "yes" or tg.get("gas:exchange") == "yes",
         "self_service": tg.get("self_service") == "yes"}
    d = {k: v for k, v in d.items() if v}
    return json.dumps(d, ensure_ascii=False, separators=(",", ":")) if d else None


def _insert(db, seq, osm, lat, lon, kind, tg):
    db.execute("INSERT INTO shop VALUES (?,?,?,?,?,?,?,?,?,?)",
               (seq, osm, lat, lon, kind, tg.get("name", ""), tg.get("brand", ""), tg.get("opening_hours", ""), _addr(tg), extra(tg)))
    db.execute("INSERT INTO shop_ix VALUES (?,?,?,?,?)", (seq, lon, lon, lat, lat))


def _source_date(src):
    """Stand der Quelldaten: Zeitstempel aus dem pbf-Header (pyosmium), sonst Datum der Datei."""
    try:
        ts = osmium.io.Reader(src).header().get("osmosis_replication_timestamp")
        if ts:
            return ts[:10]
    except Exception:  # noqa: BLE001 – Header nicht lesbar: auf Dateidatum zurueckfallen
        pass
    return datetime.datetime.fromtimestamp(os.path.getmtime(src), datetime.timezone.utc).date().isoformat()


def build(src, dst):
    """Baut dst aus der OSM-Datei src (pbf/xml); schreibt erst dst.tmp, dann atomar. Gibt die Anzahl zurueck."""
    tmp = dst + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    seq = 0
    fp = (osmium.FileProcessor(src)
          .with_locations(os.environ.get("OSM_INDEX", "flex_mem"))
          .with_areas(osmium.filter.KeyFilter("shop")))
    for o in fp:
        if o.is_node():
            if not o.tags or not o.location.valid():
                continue
            tg = dict(o.tags)
            kind = classify(tg)
            if kind:
                seq += 1
                _insert(db, seq, f"n{o.id}", o.location.lat, o.location.lon, kind, tg)
        elif o.is_area():
            tg = dict(o.tags)
            kind = classify(tg)
            if not kind:
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
            c = geom.centroid
            if not geom.contains(c):   # L-foermige Gebaeude: Punkt sicher innen
                c = geom.representative_point()
            seq += 1
            _insert(db, seq, f"{'w' if o.from_way() else 'r'}{o.orig_id()}", c.y, c.x, kind, tg)
    built = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    db.executemany("INSERT INTO meta VALUES (?,?)",
                   [("built", built), ("count", str(seq)), ("source_date", _source_date(src))])
    db.commit()
    db.close()
    os.replace(tmp, dst)
    log.info("%d Laeden -> %s", seq, dst)
    return seq


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if len(sys.argv) != 3:
        sys.exit("Aufruf: shops_build.py <quelle.osm.pbf> <shops-de.sqlite>")
    print(json.dumps({"count": build(sys.argv[1], sys.argv[2])}))
