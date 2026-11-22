"""Echte Dieselpreise ausserhalb Deutschlands (seit v0.23.0) – offene Behoerdendaten, kein Schluessel:
- FR: prix-carburants (DGCCRF) ueber data.economie.gouv.fr, Flux instantané v2 – Umkreisabfrage je Stopp, 10 min merken
- ES: Geoportal Gasolineras (Ministerio) – eine Gesamtliste aller Tankstellen, 30 min merken
- IT: MIMIT „prezzo alle 8“ + Anlagenverzeichnis – Tagesstand 8 Uhr, 6 h merken
Zuordnung wie bei Tankerkönig: OSM-Tankstelle -> naechste Station der Quelle <= 150 m.
Faellt eine Quelle aus, ruht nur sie 15 min; der Tankplan bleibt dann bei der Schaetzung."""

import csv
import io
import logging
import math
import threading
import time
from datetime import datetime

import httpx

log = logging.getLogger("fuelprice")

MATCH_M = 150
# Frankreich: Koordinaten der Betreiber oft nur auf 3 Stellen (±100 m) oder an die Adresse gelegt -> weiter suchen,
# aber nur, wenn eindeutig (keine zweite Station fast gleich nah); Preise aelter als 7 Tage zaehlen nicht
FR_MATCH_M = 400
FR_MAX_AGE = 7 * 86400
PAUSE = 900
PAUSE_SHORT = 120        # Zeitueberschreitung (FR antwortet manchmal zaeh): nur kurz ruhen
PAUSE_IT = 3600          # MIMIT nicht erreichbar: Tagesdaten, also selten neu versuchen
FR_DEADLINE = 20         # Sekunden fuer alle FR-Abrufe einer Anfrage zusammen
BACKGROUND = True        # ES/IT-Gesamtlisten im Hintergrund laden (Tests: synchron)
STALE = {"ES": 6 * 3600, "IT": 30 * 3600}   # alte Liste weiter nutzen, waehrend die neue laedt
UA = {"User-Agent": "RigAnalyzer/1 (riganalyzer.petrick.top; Tankplan)"}

FR_URL = ("https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
          "prix-des-carburants-en-france-flux-instantane-v2/records")
FR_RAD_KM = 5
FR_COVER_M = 4000
FR_MAX_CALLS = 8
FR_TTL = 600
ES_URL = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"
ES_TTL = 1800
IT_PRICES = "https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv"
IT_PLANTS = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"
IT_TTL = 6 * 3600
FRESH_TTL = 120   # „Jetzt prüfen“: FR-Umkreis/ES-Liste nur bis 2 min alt nehmen (IT bleibt Tagesstand 8 Uhr)

# grob: lat min/max, lon min/max (Ueberlappung an Grenzen schadet nicht, es zaehlt der 150-m-Treffer)
BOX = {"FR": (41.3, 51.2, -5.3, 9.7), "ES": (35.9, 43.9, -9.4, 4.4), "IT": (36.5, 47.2, 6.5, 18.6)}
SOURCE = {"FR": "prix-carburants.gouv.fr (DGCCRF, Licence Ouverte)",
          "ES": "Geoportal Gasolineras (Ministerio para la Transición Ecológica)",
          "IT": "MIMIT Osservaprezzi (IODL 2.0, Stand 8 Uhr)"}
SHORT = {"FR": "prix-carburants", "ES": "Geoportal", "IT": "MIMIT 8 Uhr"}

_paused = {}
_cache = {}
_locks = {cc: threading.Lock() for cc in ("FR", "ES", "IT")}


def _dist(la1, lo1, la2, lo2):
    k = math.pi / 180
    x = (lo2 - lo1) * k * math.cos((la1 + la2) * k / 2)
    return 6371000 * math.hypot(x, (la2 - la1) * k)


def _in(cc, lat, lon):
    b = BOX[cc]
    return b[0] <= lat <= b[1] and b[2] <= lon <= b[3]


def _num(v):
    try:
        f = float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if 0.3 < f < 5 else None


class Grid:
    """Stationen in 0,01°-Zellen, damit 12 000–23 000 Stationen schnell gefunden werden."""

    def __init__(self, items):
        self.cells = {}
        for it in items:
            self.cells.setdefault((int(it["lat"] * 100), int(it["lon"] * 100)), []).append(it)

    def near(self, lat, lon, radius=MATCH_M):
        """Naechste Station <= radius; jenseits von MATCH_M nur, wenn die zweitnaechste deutlich weiter weg ist."""
        ci, cj = int(lat * 100), int(lon * 100)
        found = []
        for i in (ci - 1, ci, ci + 1):
            for j in (cj - 1, cj, cj + 1):
                for it in self.cells.get((i, j), ()):
                    d = _dist(lat, lon, it["lat"], it["lon"])
                    if d <= radius:
                        found.append((d, it))
        if not found:
            return None
        found.sort(key=lambda x: x[0])
        d0, best = found[0]
        if d0 > MATCH_M and len(found) > 1 and found[1][0] - d0 < 150:
            return None   # zwei Stationen fast gleich weit weg: lieber schaetzen als die falsche nehmen
        return best


# ---------------------------------------------------------------- Frankreich
def _fr_fetch(lat, lon):
    r = httpx.get(FR_URL, params={
        "where": f"within_distance(geom, geom'POINT({lon:.4f} {lat:.4f})', {FR_RAD_KM}km)",
        "select": "id,geom,gazole_prix,gazole_maj,adresse,ville", "limit": 100}, headers=UA, timeout=httpx.Timeout(15, connect=5))
    r.raise_for_status()
    return r.json().get("results", [])


def _fr_parse(rows):
    out = []
    for x in rows:
        g, p = x.get("geom") or {}, _num(x.get("gazole_prix"))
        if p is None or g.get("lat") is None:
            continue
        try:
            at = int(datetime.fromisoformat(str(x.get("gazole_maj")).replace("Z", "+00:00")).timestamp())
        except ValueError:
            continue
        if time.time() - at > FR_MAX_AGE:
            continue
        out.append({"lat": float(g["lat"]), "lon": float(g["lon"]), "diesel": p, "at": at,
                    "name": " ".join(v for v in (x.get("adresse"), x.get("ville")) if v)})
    return out


def _fr_around(lat, lon, ttl=FR_TTL):
    key = ("FR", round(lat, 2), round(lon, 2))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    st = _fr_parse(_fr_fetch(key[1], key[2]))
    _cache[key] = (time.time(), st)
    if len(_cache) > 600:
        _cache.pop(next(k for k in _cache if k[0] == "FR"))
    return st


def _fr(points, out, fresh=False):
    todo, calls, t0 = points, 0, time.time()
    while todo and calls < FR_MAX_CALLS and time.time() - t0 < FR_DEADLINE:
        _, clat, clon = todo[0]
        calls += 1
        g = Grid(_fr_around(clat, clon, FRESH_TTL if fresh else FR_TTL))
        rest = []
        for sid, lat, lon in todo:
            if _dist(clat, clon, lat, lon) > FR_COVER_M:
                rest.append((sid, lat, lon))
            elif (s := g.near(lat, lon, FR_MATCH_M)):
                out[sid] = _hit(s, "FR")
        todo = rest


# ---------------------------------------------------------------- Spanien
def _es_fetch():
    r = httpx.get(ES_URL, headers={**UA, "Accept": "application/json"}, timeout=httpx.Timeout(60, connect=10))
    r.raise_for_status()
    return r.json()


def _es_parse(data):
    at = None
    try:
        at = int(datetime.strptime(data.get("Fecha", ""), "%d/%m/%Y %H:%M:%S").timestamp())
    except ValueError:
        pass
    out = []
    for x in data.get("ListaEESSPrecio", []):
        p = _num(x.get("Precio Gasoleo A"))
        la, lo = _num_ll(x.get("Latitud")), _num_ll(x.get("Longitud (WGS84)"))
        if p is None or la is None or lo is None:
            continue
        out.append({"lat": la, "lon": lo, "diesel": p, "at": at,
                    "name": " ".join(v.strip() for v in (x.get("Rótulo") or "", x.get("Localidad") or "") if v.strip())})
    return out


def _num_ll(v):
    try:
        return float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- Italien
def _it_fetch():
    a = httpx.get(IT_PRICES, headers=UA, timeout=httpx.Timeout(90, connect=10))
    a.raise_for_status()
    b = httpx.get(IT_PLANTS, headers=UA, timeout=httpx.Timeout(90, connect=10))
    b.raise_for_status()
    return a.text, b.text


def _rows(text):
    """MIMIT-CSV: erste Zeile „Estrazione del …“, dann Kopf; Trenner ';' (frueher) oder '|' (seit 2026) – beides."""
    lines = text.splitlines()
    hi = next((i for i, ln in enumerate(lines) if "idImpianto" in ln), None)
    if hi is None:
        return []
    sep = "|" if lines[hi].count("|") > lines[hi].count(";") else ";"
    rd = csv.reader(io.StringIO("\n".join(lines[hi:])), delimiter=sep, quotechar='"')
    head = [h.strip().lower() for h in next(rd)]
    return [dict(zip(head, (c.strip() for c in row))) for row in rd if row]


def _it_parse(prices_txt, plants_txt):
    best = {}
    for r in _rows(prices_txt):
        if r.get("desccarburante", "").strip().lower() != "gasolio":
            continue
        p = _num(r.get("prezzo"))
        if p is None:
            continue
        sid, self_ = r.get("idimpianto"), r.get("isself") == "1"
        old = best.get(sid)
        # Selbstbedienung zaehlt (Bedienung kostet in Italien oft 20–40 Cent mehr)
        if old is None or (self_ and not old[1]) or (self_ == old[1] and p < old[0]):
            best[sid] = (p, self_, r.get("dtcomu", ""))
    out = []
    for r in _rows(plants_txt):
        b = best.get(r.get("idimpianto"))
        la, lo = _num_ll(r.get("latitudine")), _num_ll(r.get("longitudine"))
        if not b or la is None or lo is None or not (35 < la < 48 and 6 < lo < 19):
            continue
        at = None
        try:
            at = int(datetime.strptime(b[2], "%d/%m/%Y %H:%M:%S").timestamp())
        except ValueError:
            pass
        out.append({"lat": la, "lon": lo, "diesel": b[0], "at": at, "self": b[1],
                    "name": " ".join(v for v in (r.get("bandiera", ""), r.get("comune", "")) if v)})
    return out


# ---------------------------------------------------------------- Gesamtlisten (ES, IT)
_grids = {}
_loading = set()
ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration)


def _pause(cc, e):
    sec = PAUSE_IT if cc == "IT" else PAUSE_SHORT if isinstance(e, httpx.TimeoutException) else PAUSE
    _paused[cc] = time.time() + sec
    log.warning("Dieselpreise %s: %s – %d min Pause", cc, type(e).__name__, sec // 60)


def _load_bg(cc, load):
    """Gesamtliste im Hintergrund holen (ES ~11 s, IT zwei Tagesdateien) – die Anfrage wartet nicht darauf."""
    with _locks[cc]:
        if cc in _loading:
            return
        _loading.add(cc)

    def run():
        try:
            _grids[cc] = (time.time(), Grid(load()))
        except ERRORS as e:
            _pause(cc, e)
        finally:
            _loading.discard(cc)
    threading.Thread(target=run, name=f"fuel-{cc}", daemon=True).start()


def _grid(cc, fresh=False):
    """Fertige Liste oder None (dann laedt sie gerade im Hintergrund)."""
    ttl, load = {"ES": (ES_TTL, lambda: _es_parse(_es_fetch())), "IT": (IT_TTL, lambda: _it_parse(*_it_fetch()))}[cc]
    if fresh and cc == "ES":
        ttl = FRESH_TTL
    hit = _grids.get(cc)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if BACKGROUND:
        _load_bg(cc, load)
        return hit[1] if hit and time.time() - hit[0] < STALE[cc] else None
    with _locks[cc]:
        hit = _grids.get(cc)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        g = Grid(load())
        _grids[cc] = (time.time(), g)
        return g


def _hit(s, cc):
    return {"diesel": s["diesel"], "open": None, "name": s.get("name", ""), "at": s.get("at") or int(time.time()),
            "src": SHORT[cc], "cc": cc}


def prices(stations, fresh=False):
    """stations: [(osm_id, lat, lon)] (noch ohne Preis) -> {osm_id: {diesel, open, name, at, src, cc}}."""
    return lookup(stations, fresh)[0]


def lookup(stations, fresh=False):
    """Wie prices(), dazu die Laender, deren Liste gerade noch laedt (die Seite fragt dann spaeter nochmal)."""
    out, pending = {}, set()
    for cc in ("FR", "ES", "IT"):
        pts = [s for s in stations if s[0] not in out and _in(cc, s[1], s[2])]
        if not pts or time.time() < _paused.get(cc, 0):
            continue
        try:
            if cc == "FR":
                _fr(pts, out, fresh)
            else:
                g = _grid(cc, fresh)
                if g is None:
                    if time.time() >= _paused.get(cc, 0):
                        pending.add(cc)
                    continue
                for sid, lat, lon in pts:
                    if (s := g.near(lat, lon)):
                        out[sid] = _hit(s, cc)
        except ERRORS as e:
            _pause(cc, e)
    return out, pending
