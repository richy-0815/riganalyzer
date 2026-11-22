"""Laden-Abfrage entlang einer Route fuer „Einkaufen unterwegs“ (Rig).

Datensatz: DATA_DIR/shops/shops-de.sqlite (gebaut von shops_build.py). Wird bei neuer mtime neu geladen.
"""

import json
import logging
import math
import os
import sqlite3
import threading

import numpy as np
import shapely

import poi

log = logging.getLogger("shops")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DE_HINT = "Einkaufen: bisher nur Deutschland"
MISSING_HINT = "Einkaufsdaten fehlen"
SHOP_LABEL = {"discount": "Discounter", "supermarket": "Supermarkt", "bakery": "Bäcker", "butcher": "Metzger", "farm": "Hofladen",
              # Service unterwegs – eigene Gruppe, nicht im Reiter „Einkaufen“
              "repair": "Kfz-Werkstatt", "truck_repair": "Lkw-Werkstatt", "caravan": "Caravan & Wohnmobil",
              "gas": "Gasflaschen", "laundry": "Waschsalon"}
SERVICE_KINDS = {"repair", "truck_repair", "caravan", "gas", "laundry"}
DE_BBOX = (47.2, 55.1, 5.8, 15.1)  # lat min/max, lon min/max (grob)
_STEP_M = 3000.0  # Vorfilter-Kaestchen je ~3 km Linie
_SEG_M = 10000.0  # laengere Segmente werden unterteilt
_CHUNK_M = 20000.0  # Laenge eines Projektions-Stuecks


def _path():
    return os.path.join(DATA_DIR, "shops", "shops-de.sqlite")


def touches_germany(line):
    """True, wenn ein Routenpunkt (lat, lon) in der groben DE-Bounding-Box liegt."""
    return any(DE_BBOX[0] <= la <= DE_BBOX[1] and DE_BBOX[2] <= lo <= DE_BBOX[3] for la, lo in line)


def _densify(pts, max_seg_m=_SEG_M):
    """Segmente laenger als max_seg_m per linearer Interpolation in lat/lon unterteilen, damit das Chunking
    (und damit die lokale Projektion) auch bei duennen Linien greift. -> (lat, lon) als Arrays."""
    lat, lon = pts[:, 0], pts[:, 1]
    mid = np.radians((lat[1:] + lat[:-1]) / 2)
    seg = np.hypot(np.diff(lon) * np.cos(mid) * 111320.0, np.diff(lat) * 110574.0)
    parts_la, parts_lo = [], []
    for i, d in enumerate(seg):
        k = max(1, int(math.ceil(d / max_seg_m)))
        t = np.arange(k) / k
        parts_la.append(lat[i] + (lat[i + 1] - lat[i]) * t)
        parts_lo.append(lon[i] + (lon[i + 1] - lon[i]) * t)
    parts_la.append(lat[-1:])
    parts_lo.append(lon[-1:])
    return np.concatenate(parts_la), np.concatenate(parts_lo)


class ShopDB:
    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.meta = dict(self.db.execute("SELECT key, value FROM meta"))
        self.db.execute("SELECT 1 FROM shop_ix LIMIT 1")
        # Spalte extra (Kontakt/Leistungen) fehlt in aelteren Datensaetzen bis zum Neubau
        self.has_extra = any(r[1] == "extra" for r in self.db.execute("PRAGMA table_info(shop)"))
        self._lock = threading.Lock()

    def _ids(self, boxes):
        ids = set()
        with self._lock:
            for x0, x1, y0, y1 in boxes:
                ids.update(r[0] for r in self.db.execute(
                    "SELECT id FROM shop_ix WHERE maxlon>=? AND minlon<=? AND maxlat>=? AND minlat<=?", (x0, x1, y0, y1)))
        return ids

    def _rows(self, ids):
        rows = []
        idl = sorted(ids)
        with self._lock:
            for i in range(0, len(idl), 500):
                part = idl[i:i + 500]
                rows += self.db.execute(
                    f"SELECT osm, lat, lon, kind, name, brand, hours, addr, {'extra' if self.has_extra else 'NULL'} FROM shop WHERE id IN (%s)"  # noqa: S608 – nur Platzhalter
                    % ",".join("?" * len(part)),
                    part).fetchall()
        return rows

    def near(self, line, max_m=2000):
        """Laeden bis max_m (m) neben der Route. line: Liste von (lat, lon)-Tupeln, mind. 2 Punkte.
        -> [{osm, lat, lon, kind, name, brand, hours, addr, off (m, int), km (entlang der Linie, 2 Stellen)}] nach km.
        Die Linie wird in Stuecke von ~_CHUNK_M geteilt, jedes in einer eigenen lokalen Metrik-Projektion
        (ein globales lat0 verzerrt Ost-West auf langen Nord-Sued-Routen um Prozente)."""
        if len(line) < 2:
            return []
        lat, lon = _densify(np.asarray(line, float))
        mid = np.radians((lat[1:] + lat[:-1]) / 2)
        seg = np.hypot(np.diff(lon) * np.cos(mid) * 111320.0, np.diff(lat) * 110574.0)
        cum = np.concatenate([[0.0], np.cumsum(seg)])  # m ab Start, je Segment lokal korrekt
        n = len(lat)
        starts = [0] + [int(i) for i in np.searchsorted(cum, np.arange(_CHUNK_M, cum[-1], _CHUNK_M))]
        starts = sorted(set(min(s, n - 2) for s in starts))
        best = {}  # osm -> (dist, km_m, row)
        for ci, a in enumerate(starts):
            b = starts[ci + 1] if ci + 1 < len(starts) else n - 1
            a0, b0 = max(a - 1, 0), min(b + 1, n - 1)  # Ueberlappung an den Nahtstellen
            if b0 <= a0:
                continue
            clat, clon = lat[a0:b0 + 1], lon[a0:b0 + 1]
            lat0, lon0 = float(clat.mean()), float(clon.mean())
            kx, ky = math.cos(math.radians(lat0)) * 111320.0, 110574.0
            x, y = (clon - lon0) * kx, (clat - lat0) * ky
            ccum = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
            cuts = np.unique(np.concatenate([[0, len(x) - 1], np.searchsorted(ccum, np.arange(_STEP_M, ccum[-1], _STEP_M))]))
            dlat, dlon = max_m / ky, max_m / kx
            boxes = []
            for i, j in zip(cuts[:-1], cuts[1:]):
                sl = slice(int(i), int(j) + 1)
                boxes.append((clon[sl].min() - dlon, clon[sl].max() + dlon, clat[sl].min() - dlat, clat[sl].max() + dlat))
            ids = self._ids(boxes)
            if not ids:
                continue
            rows = self._rows(ids)
            line_g = shapely.linestrings(np.column_stack([x, y]))
            px = (np.array([r[2] for r in rows]) - lon0) * kx
            py = (np.array([r[1] for r in rows]) - lat0) * ky
            sp = shapely.points(np.column_stack([px, py]))
            dist = shapely.distance(sp, line_g)
            along = shapely.line_locate_point(line_g, sp)
            # lokale Laenge des Stuecks auf die exakte Segmentsumme skalieren, dann Start-Offset addieren
            scale = (cum[b0] - cum[a0]) / ccum[-1] if ccum[-1] else 1.0
            for r, d, al in zip(rows, dist, along):
                if d <= max_m and (r[0] not in best or d < best[r[0]][0]):
                    best[r[0]] = (float(d), cum[a0] + float(al) * scale, r)
        out = [{"osm": r[0], "lat": r[1], "lon": r[2], "kind": r[3], "name": r[4], "brand": r[5], "hours": r[6],
                "addr": r[7], "extra": _extra(r[8]), "off": int(round(d)), "km": round(km / 1000, 2)} for d, km, r in best.values()]
        out.sort(key=lambda s: s["km"])
        return out


def _extra(raw):
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


_db = None
_db_key = None
_lock = threading.Lock()


def get():
    """Gemeinsame Instanz; laedt neu, sobald die Datei neu/ersetzt ist. Fehlt oder ist kaputt -> None."""
    global _db, _db_key
    f = _path()
    try:
        st = os.stat(f)
        key = (f, st.st_mtime, st.st_ino, st.st_size)
    except OSError:
        key = None
    with _lock:
        if key != _db_key:
            # Die alte ShopDB wird bewusst nicht close()d: parallele near()-Aufrufe halten sie evtl. noch;
            # der GC schliesst sie, und das ersetzte File bleibt per Inode lesbar.
            _db = None
            if key:
                try:
                    _db = ShopDB(f)
                    log.info("Laden-Daten geladen: Stand %s, %s Laeden", _db.meta.get("built"), _db.meta.get("count"))
                except Exception as e:  # noqa: BLE001 – kaputte Datei verhalten wie fehlende
                    log.warning("Laden-DB %s unbrauchbar: %s", f, e)
            _db_key = key
        return _db


def route_pois(plat, plon, notes):
    """Rig: Einkaufsmoeglichkeiten (Gruppe shop) bis 2 km neben der Route; ein Abruf je Analyse.
    Ohne Deutschland-Bezug bzw. ohne Datensatz stattdessen ein Hinweis in notes."""
    line = list(zip(plat.tolist(), plon.tolist()))

    def hint(text, detail):
        notes.append({"km": 0.0, "lat": round(float(plat[0]), 5), "lon": round(float(plon[0]), 5), "t": "shop",
                      "label": text, "text": detail})
        notes.sort(key=lambda n: n["km"])
        return []

    if not touches_germany(line):
        return hint(DE_HINT, "Läden werden bisher nur in Deutschland angezeigt.")
    missing = ("Die Einkaufsdaten sind gerade nicht verfügbar.")
    db = get()
    if db is None:
        return hint(MISSING_HINT, missing)
    try:
        found = db.near(line)
    except Exception:
        log.warning("Einkaufsdaten nicht lesbar", exc_info=True)
        return hint(MISSING_HINT, missing)
    out = []
    for s in found:
        label = SHOP_LABEL.get(s["kind"], s["kind"])
        grp = "service" if s["kind"] in SERVICE_KINDS else "shop"
        out.append({"km": s["km"], "lat": s["lat"], "lon": s["lon"], "off": s["off"], "group": grp, "label": label,
                    "name": s.get("name") or s.get("brand") or label,
                    "flags": {"kind": s["kind"], "brand": s.get("brand"), "hours": s.get("hours"), "addr": s.get("addr"),
                              **(s.get("extra") or {})},
                    "osm": poi.osm_ref(s["osm"])})
    return out
