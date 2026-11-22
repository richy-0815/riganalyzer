"""Stell- und Campingplaetze aus DATAtourisme (seit v0.24.0): Behoerdendaten der franzoesischen Tourismusaemter,
Licence Ouverte 2.0. Woechentlich die Regionsdateien (CSV) von data.gouv.fr holen, nur Campings/Aires behalten,
kompakt nach DATA_DIR/frcamps.json. Abfrage: OSM-Plaetze einer Route bekommen die Angaben des Tourismusamts
(offizielle Sterne, Beschreibung, Website, Stand), Plaetze, die in OSM fehlen, kommen als eigene Treffer dazu."""

import csv
import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time

import httpx

log = logging.getLogger("frcamps")

DATASET = "https://www.data.gouv.fr/api/1/datasets/datatourisme-la-plateforme-nationale-des-donnees-touristiques/"
FILE = os.path.join(os.environ.get("DATA_DIR", "/data"), "frcamps.json")
SOURCE = "DATAtourisme (Tourismusämter Frankreich, Licence Ouverte 2.0)"
MAX_AGE = 7 * 86400
OVERSEAS = ("reu", "myt", "mtq", "guf", "glp")
KINDS = {"CamperVanArea": "aire", "RVServiceArea": "service", "FarmCamping": "farm", "NaturalCampingArea": "nature",
         "Camping": "camping"}
MATCH_M = 250          # OSM-Platz <-> Eintrag des Tourismusamts
MATCH_NAME_M = 700     # weiter weg nur mit gemeinsamem Namensteil
EXTRA_M = 1500         # fehlt in OSM: bis so weit neben der Route anzeigen
UA = {"User-Agent": "RigAnalyzer/1 (riganalyzer.petrick.top; Stellplatz-Infos)"}

FR_BOX = (41.3, 51.2, -5.3, 9.7)   # grob Frankreich mit Korsika (wie im Browser)
_data = {"t": 0.0, "items": [], "grid": {}}


def in_france(lat, lon):
    return FR_BOX[0] <= lat <= FR_BOX[1] and FR_BOX[2] <= lon <= FR_BOX[3]


_lock = threading.Lock()


def _dist(la1, lo1, la2, lo2):
    k = math.pi / 180
    x = (lo2 - lo1) * k * math.cos((la1 + la2) * k / 2)
    return 6371000 * math.hypot(x, (la2 - la1) * k)


def _kind(cats):
    names = {c.rsplit("#", 1)[-1] for c in cats.split("|")}
    for k, v in KINDS.items():
        if k in names:
            return v
    return None


def _contacts(v):
    """„Lien de réservation#https://…|#https://site.fr<>https://bit.ly/…“ -> (website, reservation)"""
    web = res = ""
    for part in (v or "").split("|"):
        label, _, url = part.partition("#")
        url = url.split("<>")[0].strip()
        if not url.startswith(("http://", "https://")):
            continue
        if "réserv" in label.lower() or "reserv" in label.lower():
            res = res or url
        else:
            web = web or url
    return web, res


def parse_csv(text):
    """Eine Regionsdatei -> kompakte Eintraege (nur Campings/Aires)."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        kind = _kind(row.get("Categories_de_POI", ""))
        if not kind:
            continue
        try:
            la, lo = float(row["Latitude"]), float(row["Longitude"])
        except (KeyError, ValueError):
            continue
        web, res = _contacts(row.get("Contacts_du_POI"))
        stars = re.search(r"(\d)\s*étoile", row.get("Classements_du_POI", ""))
        out.append({"n": (row.get("Nom_du_POI") or "").strip(), "k": kind, "la": round(la, 6), "lo": round(lo, 6),
                    "a": " ".join(x for x in ((row.get("Adresse_postale") or "").strip(),
                                              (row.get("Code_postal_et_commune") or "").replace("#", " ").strip()) if x),
                    "p": (row.get("Periodes_regroupees") or "")[:120], "u": (row.get("Date_de_mise_a_jour") or "")[:10],
                    "w": web, "r": res, "s": int(stars.group(1)) if stars else None,
                    "d": (row.get("Description") or "").strip()[:500], "id": row.get("URI_ID_du_POI", "")})
    return out


def _region_urls():
    r = httpx.get(DATASET, headers=UA, timeout=30)
    r.raise_for_status()
    res = r.json().get("resources", [])
    return [x["url"] for x in res if re.match(r"datatourisme-reg-([a-z]+)\.csv$", x.get("title", ""))
            and not x["title"].startswith(tuple(f"datatourisme-reg-{o}" for o in OVERSEAS))]


def refresh_isolated():
    """Wochenabruf in einem eigenen Prozess (0.38.4): Herunterladen und Parsen hielten im App-Prozess das GIL
    sekundenlang fest – keine Anfrage, kein Gesundheitscheck kam durch, Traefik zeigte 503. Hier wartet nur
    ein Thread auf den Kindprozess; danach wird die fertige (kleine) Datei geladen."""
    env = {**os.environ, "DATA_DIR": os.path.dirname(FILE)}
    subprocess.run([sys.executable, "-m", "frcamps"], cwd=os.path.dirname(os.path.abspath(__file__)),
                   env=env, check=True, timeout=2 * 3600)
    return len(_load(force=True)["items"])


def refresh(reload=True):
    """Alle Regionen laden (zusammen ~400 MB, je Datei gestreamt nur gelesen), Campings behalten, speichern."""
    items = []
    for url in _region_urls():
        with httpx.stream("GET", url, headers=UA, timeout=httpx.Timeout(120, connect=15)) as r:
            r.raise_for_status()
            items += parse_csv(r.read().decode("utf-8", "replace"))
    if len(items) < 1000:
        raise ValueError(f"nur {len(items)} Plätze – Format geändert?")
    tmp = FILE + ".neu"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"t": time.time(), "items": items}, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, FILE)
    if reload:
        _load(force=True)
    log.info("DATAtourisme: %d Stell-/Campingplätze", len(items))
    return len(items)


def _load(force=False):
    if _data["items"] and not force:
        return _data
    with _lock:
        try:
            with open(FILE, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return _data
        grid = {}
        for it in d["items"]:
            grid.setdefault((int(it["la"] * 50), int(it["lo"] * 50)), []).append(it)
        _data.update(t=d["t"], items=d["items"], grid=grid)
    return _data


def due():
    try:
        return time.time() - os.path.getmtime(FILE) > MAX_AGE
    except OSError:
        return True


def _near(lat, lon, radius):
    g = _load()["grid"]
    ci, cj = int(lat * 50), int(lon * 50)
    out = []
    for i in (ci - 1, ci, ci + 1):
        for j in (cj - 1, cj, cj + 1):
            for it in g.get((i, j), ()):
                d = _dist(lat, lon, it["la"], it["lo"])
                if d <= radius:
                    out.append((d, it))
    return sorted(out, key=lambda x: x[0])


_WORD = re.compile(r"[a-zà-ÿ]{4,}")
_STOP = {"camping", "aire", "camping-car", "cars", "municipal", "domaine", "parking", "service", "services", "accueil"}


def _words(s):
    return {w for w in _WORD.findall((s or "").lower()) if w not in _STOP}


def _public(it):
    return {k: it[k] for k in ("n", "k", "a", "p", "u", "w", "r", "s", "d", "id")}


def lookup(pois, line):
    """pois: [(osm, lat, lon, name)], line: [(lat, lon, km)] (ausgeduennt) ->
    {"match": {osm: Eintrag}, "extra": [Eintrag + lat/lon/km/off], "stand": Datum, "source": …}"""
    d = _load()
    if not d["items"]:
        return {"match": {}, "extra": [], "stand": None, "source": SOURCE}
    match, used = {}, set()
    for osm, lat, lon, name in pois:
        for dist, it in _near(lat, lon, MATCH_NAME_M):
            if it["id"] in used:
                continue
            if dist <= MATCH_M or (_words(name) & _words(it["n"])):
                match[osm] = _public(it)
                used.add(it["id"])
                break
    extra, seen = [], set()
    for lat, lon, km in line:
        for dist, it in _near(lat, lon, EXTRA_M):
            if it["id"] in used or it["id"] in seen or it["k"] == "service":
                continue
            seen.add(it["id"])
            extra.append({**_public(it), "lat": it["la"], "lon": it["lo"], "km": round(km, 2), "off": round(dist)})
    # je Eintrag den naechsten Linienpunkt (km/off) – grob reicht, die Linie ist ausgeduennt
    for e in extra:
        best = min(line, key=lambda p: _dist(p[0], p[1], e["lat"], e["lon"]))
        e["km"], e["off"] = round(best[2], 2), round(_dist(best[0], best[1], e["lat"], e["lon"]))
    extra = [e for e in extra if e["off"] <= EXTRA_M][:300]
    return {"match": match, "extra": extra, "stand": time.strftime("%d.%m.%Y", time.localtime(d["t"])), "source": SOURCE}


if __name__ == "__main__":   # Kindprozess von refresh_isolated()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.nice(15)
    refresh(reload=False)
