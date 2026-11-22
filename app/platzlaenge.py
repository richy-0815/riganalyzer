"""Stellplatz-Laenge (seit 0.53.0, Ticket #30): passt ein langes Fahrzeug/Gespann auf den Platz?

In OSM steht `maxlength` an Stell- und Campingplaetzen fast nie. Deshalb tragen wir je Platz zusammen – ganz ohne
KI-Dienste, es kostet nichts je Platz:

1. Text: Beschreibung in OSM, Text des franzoesischen Tourismusamts (DATAtourisme) und die Website des Platzes
   (Startseite + bis zu zwei passende Unterseiten), Suchmuster in sechs Sprachen. Fund = „bestaetigt“.
2. Luftbild-Messung: Orthofoto (IGN, PNOA, PDOK, swisstopo, basemap.at, sonst Esri) im OSM-Umriss des Platzes.
   Baeume und Schatten fallen raus, gemessen werden die laengste 3 m breite freie Bahn (Stehen) und das groesste
   freie Quadrat (Wenden). Dazu die Masse eingezeichneter Stellflaechen (camp_pitch, parking_space) und
   Beschraenkungen auf den Strassen der Zufahrt. Ergebnis = „wahrscheinlich“.

Nur fuer Plaetze, die an einer geplanten Route liegen (Abfrage aus dem Browser bzw. Nachtplatz-Vorschlaege),
Ergebnis je Platz zwischengespeichert (DATA_DIR/platzlaenge.sqlite, Bilder unter DATA_DIR/platzlaenge/).
"""

import io
import ipaddress
import json
import logging
import math
import os
import re
import socket
import sqlite3
import threading
import time
from html import unescape
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger("platzlaenge")

DATA = os.environ.get("DATA_DIR", "/data")
FILE = os.path.join(DATA, "platzlaenge.sqlite")
IMG_DIR = os.path.join(DATA, "platzlaenge")
MAX_QUEUE = 3000
TTL = 180 * 86400            # danach neu auswerten (Website/Luftbild aendern sich)
RETRY_ERR = 2 * 86400        # Fehler (Netz) nach zwei Tagen nochmal
UA = "RigAnalyzer/1 (Stellplatz-Infos; +https://riganalyzer.petrick.top)"
MIN_M, MAX_M = 5.0, 25.0     # plausible Fahrzeuglaengen
NO_LIMIT = 99.0              # „maxlength=none“: ausdruecklich keine Laengenbegrenzung

_lock = threading.Lock()
_state = {"started": False}
_tls = threading.local()


# ====================================================================== Speicher
def _db():
    con = getattr(_tls, "con", None)
    if con is None:
        os.makedirs(DATA, exist_ok=True)
        con = sqlite3.connect(FILE, timeout=30, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS result (key TEXT PRIMARY KEY, at REAL, ok INTEGER, data TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS queue (key TEXT PRIMARY KEY, prio REAL, at REAL, place TEXT)")
        _tls.con = con
    return con


def _get(key):
    row = _db().execute("SELECT at, ok, data FROM result WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    at, ok, data = row
    age = time.time() - at
    if age > TTL or (not ok and age > RETRY_ERR):
        return None
    return json.loads(data)


def _put(key, data, ok=True):
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO result VALUES (?, ?, ?, ?)", (key, time.time(), int(ok), json.dumps(data, ensure_ascii=False)))
        con.execute("DELETE FROM queue WHERE key = ?", (key,))


def valid_key(key):
    return bool(re.fullmatch(r"(node|way|relation)/\d{1,12}|dt:[\w.:/-]{1,120}", key or ""))


# ====================================================================== 1. Text
_NUM = r"(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:m\b|mètres?|metres?|meters?|meter\b|metri\b|metros?\b|mtr)"
_LEN_WORDS = re.compile(
    r"läng|lang\b|lange\b|langen\b|fahrzeug|wohnmobil|womo|gespann|reisemobil|stellpl|wohnwag|caravan|"
    r"longueur|long\b|longs\b|camping-car|véhicule|vehicule|emplacement|"
    r"length|long\b|vehicle|motorhome|camper|rv\b|pitch|"
    r"lunghezz|mezz|camper|autocaravan|piazzol|longitud|vehículo|parcela|lengte|voertuig|camper|plaats", re.I)
_DIST_WORDS = re.compile(r"\b(zum|zur|vom|von der|entfernt|strand|zentrum|plage|centre|du centre|from the|away|"
                         r"to the|beach|dal|dalla|spiaggia|playa|del centro|van het|strand)\b|\bm\s+(?:de|from|vom|von|zum|à|a)\b", re.I)
# Obergrenze: „bis 10 m“, „max. 9 m“, „jusqu'à 12 m“, „up to 8 m“, „fino a 10 m“, „hasta 9 m“, „tot 10 m“
_MAX_PAT = re.compile(r"(?:bis\s+(?:zu\s+)?|max(?:imal|imum|\.)?\s*(?:länge|length|longueur)?\s*:?\s*|höchstens\s+|"
                      r"jusqu['’]à\s+|maxi(?:mum|male)?\s*:?\s*|up\s+to\s+|fino\s+a\s+|massim[ao]\s+|hasta\s+|tot\s+|"
                      r"maximale\s+lengte\s*:?\s*|longitud\s+máxima\s*:?\s*|lunghezza\s+massima\s*:?\s*|"
                      r"≤\s*|<=?\s*)" + _NUM, re.I)
# „über 8 m nicht“, „plus de 8 m interdits“, „over 10 m not allowed“
_OVER_PAT = re.compile(r"(?:über|ueber|ab|mehr\s+als|länger\s+als|plus\s+de|de\s+plus\s+de|over|more\s+than|longer\s+than|"
                       r"oltre|superiori?\s+a|más\s+de|langer\s+dan|meer\s+dan|>\s*)\s*" + _NUM, re.I)
_NEG = re.compile(r"\bnicht\b|\bkein|ungeeignet|verboten|untersagt|unzulässig|interdit|non\s+admis|pas\s+admis|pas\s+accept|"
                  r"refus|\bnot\b|\bno\b|prohibited|forbidden|vietat|non\s+ammess|no\s+admit|prohibid|niet\s+toegestaan|verboden", re.I)
_POS = re.compile(r"geeignet|möglich|willkommen|auch|bienvenu|possible|accept|welcome|suitable|allowed|benvenut|"
                  r"adatt|bienvenid|admit|welkom|geschikt|platz\s+für|places?\s+pour|pitches\s+for", re.I)
_TRAILER_NO = re.compile(r"kein(?:e)?\s+(?:gespanne|anhänger|wohnwagen)|(?:gespanne|anhänger|wohnwagen)\s+(?:sind\s+)?nicht|"
                         r"remorques?\s+(?:sont\s+)?interdit|pas\s+de\s+remorque|caravanes?\s+(?:sont\s+)?interdit|"
                         r"no\s+(?:trailers|caravans)|trailers?\s+(?:are\s+)?not|rimorchi\s+non|no\s+se\s+admiten\s+remolques|"
                         r"geen\s+(?:aanhangers|caravans)", re.I)


def _f(s):
    return float(s.replace(",", "."))


_ABBR = re.compile(r"(?:\b(?:max|maxi|ca|bzw|inkl|zzgl|z\.\s?b|u\.\s?a|env|approx|mind|min|lt|lg|long|st|nr|no|tel)\.)$", re.I)


def _sentences(text):
    out = []
    for part in re.split(r"(?<=[.!?;])\s+|\n+|\s•\s|\s\|\s", text or ""):
        part = part.strip()
        if not part:
            continue
        if out and _ABBR.search(out[-1]):      # „Max. Länge: 10 m“ nicht nach „Max.“ trennen
            out[-1] += " " + part
        else:
            out.append(part)
    return out


def from_text(text):
    """Text -> {"kind": "max"|"min", "m": Laenge, "quote": Satz} oder None; dazu "trailer": False bei Gespann-Verbot.
    max: laenger nicht (Fahrzeuge bis m passen) · min: ausdruecklich auch fuer Fahrzeuge dieser Laenge."""
    best = None
    trailer = None
    for s in _sentences(text):
        if _TRAILER_NO.search(s):
            trailer = False
        if not _LEN_WORDS.search(s):
            continue
        for m in _OVER_PAT.finditer(s):
            v = _f(m.group(1))
            rest = s[m.end():m.end() + 60]
            if not (MIN_M <= v <= MAX_M) or _DIST_WORDS.search(s[m.end():m.end() + 25]):
                continue
            if _NEG.search(rest) or _NEG.search(s[max(0, m.start() - 40):m.start()]):
                cand = {"kind": "max", "m": v, "quote": s[:300]}
            elif _POS.search(s):
                cand = {"kind": "min", "m": v, "quote": s[:300]}
            else:
                continue
            best = _better(best, cand)
        for m in _MAX_PAT.finditer(s):
            v = _f(m.group(1))
            if not (MIN_M <= v <= MAX_M) or _DIST_WORDS.search(s[m.end():m.end() + 25]):
                continue
            best = _better(best, {"kind": "max", "m": v, "quote": s[:300]})
    if best is None and trailer is None:
        return None
    out = dict(best or {})
    if trailer is False:
        out["trailer"] = False
    return out


def _better(a, b):
    """Strengere Angabe gewinnt: Obergrenze vor Mindestangabe, kleinere Obergrenze vor groesserer."""
    if a is None:
        return b
    if a["kind"] != b["kind"]:
        return a if a["kind"] == "max" else b
    if a["kind"] == "max":
        return a if a["m"] <= b["m"] else b
    return a if a["m"] >= b["m"] else b


# ====================================================================== Website
class Blocked(Exception):
    pass


def _public_host(host):
    """Nur oeffentliche Adressen (keine internen Netze, kein localhost) – Websites stehen in fremden Daten."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return bool(infos)


def _fetch(url, client, max_bytes=1_500_000):
    for _ in range(4):
        u = urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname or not _public_host(u.hostname):
            raise Blocked(url)
        with client.stream("GET", url) as r:
            if r.is_redirect:
                url = urljoin(url, r.headers.get("location", ""))
                continue
            r.raise_for_status()
            if not (r.headers.get("content-type", "").startswith(("text/html", "text/plain", "application/xhtml"))):
                return url, ""
            buf = b""
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > max_bytes:
                    break
            return url, buf.decode(r.encoding or "utf-8", "replace")
    raise Blocked(url)


def _robots_ok(url, client):
    u = urlsplit(url)
    rp = RobotFileParser()
    try:
        _, txt = _fetch(f"{u.scheme}://{u.netloc}/robots.txt", client, 200_000)
    except (httpx.HTTPError, Blocked):
        return True                 # keine robots.txt -> erlaubt
    rp.parse(txt.splitlines())
    return rp.can_fetch("RigAnalyzer", url)


def html_text(html):
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|li|div|h\d|tr|td|section)>", "\n", html)
    return re.sub(r"[ \t\r\f\v]+", " ", unescape(re.sub(r"(?s)<[^>]+>", " ", html))).strip()


_SUB = re.compile(r"stellpl|wohnmobil|womo|camping-?car|aire|tarif|preis|prix|emplacement|pitch|piazzol|parcel|"
                  r"plaats|info|anfahrt|acc[eè]s|camper|mobil", re.I)


def _subpages(base, html):
    out = []
    for href, label in re.findall(r"""(?is)<a[^>]+href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", html):
        if not (_SUB.search(href) or _SUB.search(label)):
            continue
        url = urljoin(base, href)
        if urlsplit(url).netloc == urlsplit(base).netloc and url not in out and url != base:
            out.append(url)
    return out[:2]


def website_text(url):
    """Startseite + bis zu zwei passende Unterseiten -> [(url, text)]; [] bei Sperre/Fehler."""
    pages = []
    with httpx.Client(timeout=httpx.Timeout(15, connect=8), headers={"User-Agent": UA}, follow_redirects=False) as c:
        try:
            if not _robots_ok(url, c):
                return []
            final, html = _fetch(url, c)
        except (httpx.HTTPError, Blocked, ValueError) as e:
            log.info("Website %s: %s", url, type(e).__name__)
            return []
        pages.append((final, html_text(html)))
        for sub in _subpages(final, html):
            try:
                if _robots_ok(sub, c):
                    u2, h2 = _fetch(sub, c)
                    pages.append((u2, html_text(h2)))
            except (httpx.HTTPError, Blocked, ValueError):
                continue
            time.sleep(1)
    return pages


# ====================================================================== 2. Luftbild
# Quellen in Reihenfolge: nationale Orthofotos (offen, ohne Konto, 10–30 cm), sonst Esri World Imagery
SOURCES = [
    {"id": "ign", "name": "IGN BD ORTHO (Licence Ouverte 2.0)", "box": (41.3, 51.2, -5.3, 9.7), "z": 19,
     "url": "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS"
            "&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/jpeg"},
    {"id": "pnoa", "name": "PNOA © IGN España (CC BY 4.0)", "box": (35.9, 43.9, -9.5, 4.4), "z": 20,
     "url": "https://www.ign.es/wmts/pnoa-ma?request=GetTile&service=WMTS&version=1.0.0&layer=OI.OrthoimageCoverage"
            "&style=default&tilematrixset=GoogleMapsCompatible&tilematrix={z}&tilerow={y}&tilecol={x}&format=image/jpeg"},
    {"id": "pdok", "name": "Luchtfoto PDOK / Beeldmateriaal.nl (CC BY 4.0)", "box": (50.7, 53.6, 3.3, 7.3), "z": 20,
     "url": "https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/Actueel_orthoHR/EPSG:3857/{z}/{x}/{y}.jpeg"},
    {"id": "swisstopo", "name": "SWISSIMAGE © swisstopo", "box": (45.8, 47.9, 5.9, 10.5), "z": 20,
     "url": "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg"},
    {"id": "basemap", "name": "basemap.at Orthofoto (CC BY 4.0)", "box": (46.3, 49.1, 9.5, 17.2), "z": 19,
     "url": "https://mapsneu.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg"},
    {"id": "esri", "name": "Esri World Imagery (Esri, Vantor, Earthstar Geographics)", "box": (-90, 90, -180, 180), "z": 19,
     "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"},
]
TILE = 256
MAX_PX = 1200                 # laengste Bildseite des Mosaiks
MEAS_MPP = 0.4                # Messraster (m/px): reicht fuer Meter-Aussagen, braucht ein Viertel des Speichers
VEH_W = 3.0                   # Breite einer Stell-/Fahrbahn fuer die Messung (Fahrzeug + Spiegel + Luft)


def _mx(lon, z):
    return (lon + 180) / 360 * (2 ** z) * TILE


def _my(lat, z):
    return (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * (2 ** z) * TILE


def m_per_px(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


def _tile(client, src, z, x, y):
    try:
        r = client.get(src["url"].format(z=z, x=x, y=y))
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        return None
    return r.content


def _blank(img):
    """Einfarbige Kachel (ausserhalb der Abdeckung liefern manche Dienste Weiss/Grau)."""
    lo, hi = img.convert("L").getextrema()
    return hi - lo < 12


def aerial(lat, lon, bbox, client=None):
    """Orthofoto um den Platz -> {"img": PIL-Bild, "src", "z", "mpp", "px": (lat, lon) -> (x, y)} oder None.
    bbox: (s, n, w, e) inkl. Rand. Nimmt die erste Quelle, die hier echte (nicht einfarbige) Kacheln liefert."""
    from PIL import Image
    own = client is None
    client = client or httpx.Client(timeout=15, headers={"User-Agent": UA})
    try:
        for src in SOURCES:
            b = src["box"]
            if not (b[0] <= lat <= b[1] and b[2] <= lon <= b[3]):
                continue
            z = src["z"]
            while z > 15 and max(_mx(bbox[3], z) - _mx(bbox[2], z), _my(bbox[0], z) - _my(bbox[1], z)) > MAX_PX:
                z -= 1
            x0, x1 = int(_mx(bbox[2], z) // TILE), int(_mx(bbox[3], z) // TILE)
            y0, y1 = int(_my(bbox[1], z) // TILE), int(_my(bbox[0], z) // TILE)
            if (x1 - x0 + 1) * (y1 - y0 + 1) > 64:
                continue
            first = _tile(client, src, z, int(_mx(lon, z) // TILE), int(_my(lat, z) // TILE))
            if not first or _blank(Image.open(io.BytesIO(first))):
                continue
            mosaic = Image.new("RGB", ((x1 - x0 + 1) * TILE, (y1 - y0 + 1) * TILE), (128, 128, 128))
            for tx in range(x0, x1 + 1):
                for ty in range(y0, y1 + 1):
                    raw = _tile(client, src, z, tx, ty)
                    if raw:
                        mosaic.paste(Image.open(io.BytesIO(raw)).convert("RGB"), ((tx - x0) * TILE, (ty - y0) * TILE))
            cx, cy = _mx(bbox[2], z), _my(bbox[1], z)
            crop = (int(cx - x0 * TILE), int(cy - y0 * TILE), int(_mx(bbox[3], z) - x0 * TILE), int(_my(bbox[0], z) - y0 * TILE))
            ox, oy = x0 * TILE + crop[0], y0 * TILE + crop[1]

            def px(la, lo, _z=z, _ox=ox, _oy=oy):
                return _mx(lo, _z) - _ox, _my(la, _z) - _oy
            return {"img": mosaic.crop(crop), "src": src, "z": z, "mpp": m_per_px(lat, z), "px": px}
    finally:
        if own:
            client.close()
    return None


# ---------------------------------------------------------------------- Messung im Bild (ohne KI)
def open_mask(img):
    """Befahrbar wirkende Flaeche: alles ausser Baeumen/Hecken (dunkles Gruen) und Schatten.
    Wiese, Schotter, Asphalt und abgestellte Fahrzeuge zaehlen als offen."""
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    v = a.max(2)
    tree = ((2 * g - r - b) > 12) & (v < 150)
    shade = v < 85
    return ~(tree | shade)


def _box_sum(m, k):
    """Summe je k x k-Fenster (zentriert, am Rand abgeschnitten) ueber ein Integralbild -> (summe, flaeche)."""
    import numpy as np
    ii = np.pad(m.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    h, w = m.shape
    r = k // 2
    y0, y1 = np.clip(np.arange(h) - r, 0, h), np.clip(np.arange(h) + r + 1, 0, h)
    x0, x1 = np.clip(np.arange(w) - r, 0, w), np.clip(np.arange(w) + r + 1, 0, w)
    s = ii[y1][:, x1] - ii[y0][:, x1] - ii[y1][:, x0] + ii[y0][:, x0]
    return s, (y1 - y0)[:, None] * (x1 - x0)[None, :]


def smooth(m, k):
    """Mehrheitsfilter k x k: einzelne Pixel/Loecher (Laternen, Lichtflecken) verschwinden."""
    s, area = _box_sum(m, k)
    return s * 2 > area


def _corridor(m, k):
    """True, wo die k Pixel ab hier nach unten alle True sind (eine k breite Bahn passt waagerecht)."""
    import numpy as np
    c = np.pad(m.astype(np.int32), ((1, 0), (0, 0))).cumsum(0)
    out = np.zeros(m.shape, bool)
    if m.shape[0] >= k:
        out[: m.shape[0] - k + 1] = (c[k:] - c[:-k]) == k
    return out


def _longest_run(m):
    """Laengster waagerechter True-Lauf -> (laenge, zeile, start)."""
    import numpy as np
    best = (0, 0, 0)
    pad = np.zeros((m.shape[0], 1), np.int8)
    d = np.diff(np.hstack([pad, m.astype(np.int8), pad]), axis=1)
    for y in np.nonzero(m.any(1))[0]:
        s, e = np.nonzero(d[y] == 1)[0], np.nonzero(d[y] == -1)[0]
        i = int(np.argmax(e - s))
        if e[i] - s[i] > best[0]:
            best = (int(e[i] - s[i]), int(y), int(s[i]))
    return best


def longest_strip(m, width_px, step=10):
    """Laengstes width_px breites Rechteck in der Maske, Richtung in step-Grad-Schritten ->
    (laenge_px, [4 Ecken (x, y) im Originalbild])."""
    import numpy as np
    from PIL import Image
    h, w = m.shape
    best = (0, [])
    for ang in range(0, 180, step):
        rot = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).rotate(ang, expand=True, resample=Image.NEAREST)) > 0
        n, y, x = _longest_run(_corridor(rot, width_px))
        if n <= best[0]:
            continue
        # Ecken zurueckdrehen: PIL dreht gegen den Uhrzeigersinn um die Bildmitte, mit expand verschoben
        rh, rw = rot.shape
        t = math.radians(ang)
        pts = []
        for px_, py_ in ((x, y), (x + n, y), (x + n, y + width_px), (x, y + width_px)):
            dx, dy = px_ - rw / 2, py_ - rh / 2
            pts.append((w / 2 + dx * math.cos(t) - dy * math.sin(t), h / 2 + dx * math.sin(t) + dy * math.cos(t)))
        best = (n, pts)
    return best


def largest_square(m):
    """Seitenlaenge (px) des groessten achsparallelen Quadrats aus lauter True (Binaersuche ueber ein Integralbild)."""
    import numpy as np
    ii = np.pad(m.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    lo, hi = 0, min(m.shape)
    while lo < hi:
        k = (lo + hi + 1) // 2
        s = ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]
        if (s == k * k).any():
            lo = k
        else:
            hi = k - 1
    return lo


def measure(shot, outline, pitches=()):
    """Luftbild + OSM-Umriss -> Messwerte + Bild mit Einzeichnungen (JPEG).
    stellbahn_m: laengste 3 m breite freie Bahn auf dem Platz · rangier_m: groesstes freies Quadrat ·
    offen: Anteil befahrbar wirkender Flaeche · flaeche_m2: Platzflaeche laut Umriss."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    img, mpp, px = shot["img"], shot["mpp"], shot["px"]
    poly = [px(la, lo) for la, lo in outline]
    inside_img = Image.new("L", img.size, 0)
    ImageDraw.Draw(inside_img).polygon(poly, fill=255)
    inside = np.asarray(inside_img) > 0
    om = smooth(open_mask(img), max(3, int(1.0 / mpp) | 1)) & inside
    # gemessen wird auf groeberem Raster (Speicher: Drehen in 18 Richtungen kostete bei 0,2 m/px ~250 MB)
    f = max(1, round(MEAS_MPP / mpp))
    small = np.asarray(Image.fromarray(om.astype(np.uint8) * 255).reduce(f)) > 127 if f > 1 else om
    strip_px, corners = longest_strip(small, max(2, round(VEH_W / (mpp * f))))
    sq_px = largest_square(small)
    strip_px, sq_px, corners = strip_px * f, sq_px * f, [(x * f, y * f) for x, y in corners]
    Image.core.set_blocks_max(0)   # Pillow gibt seine Zwischenspeicher frei (Auswerter laeuft lange)
    Image.core.set_blocks_max(64)
    area = float(inside.sum()) * mpp * mpp
    res = {"stellbahn_m": round(strip_px * mpp, 1), "rangier_m": round(sq_px * mpp, 1),
           "offen": round(float(om.sum()) / max(1, int(inside.sum())), 2), "flaeche_m2": round(area)}
    # Bild zum Nachpruefen: offene Flaeche leicht eingefaerbt, laengste Bahn rot, Umriss gelb, Stellflaechen tuerkis
    vis = np.asarray(img.convert("RGB")).copy()
    vis[om] = (vis[om] * 0.7 + np.array([255, 0, 255]) * 0.3).astype(np.uint8)
    out = Image.fromarray(vis)
    d = ImageDraw.Draw(out)
    for p in pitches:
        if len(p) > 1:
            d.line([px(la, lo) for la, lo in p], fill=(0, 230, 230), width=2)
    d.line(poly + poly[:1], fill=(255, 220, 0), width=3)
    if corners:
        d.polygon(corners, outline=(255, 30, 30), width=3)
    meters = 20 if 20 / mpp < out.width / 3 else 10
    bar, h = meters / mpp, out.height
    font = ImageFont.load_default(size=18)
    d.rectangle((6, h - 40, 26 + bar + 64, h - 6), fill=(0, 0, 0))
    d.line((14, h - 18, 14 + bar, h - 18), fill=(255, 255, 255), width=5)
    for k in range(0, meters + 1, 5):
        xk = 14 + bar * k / meters
        d.line((xk, h - 30, xk, h - 12), fill=(255, 255, 255), width=2)
    d.text((22 + bar, h - 34), f"{meters} m", fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=85)
    return res, buf.getvalue()


def image_estimate(m):
    """Messwerte -> geschaetzte Hoechstlaenge (m) oder None.
    Stehen: laengste freie Bahn minus 1 m Luft. Wenden: im groessten freien Quadrat dreht ein Fahrzeug, das hoechstens
    knapp so lang ist wie die Seite (+1 m, vorsichtige Faustregel Wendekreis). Ab 18 m sagt die Messung nichts Genaueres mehr."""
    if not m or m.get("flaeche_m2", 0) < 150 or m.get("offen", 0) < 0.15:
        return None
    return round(min(m["stellbahn_m"] - 1.0, m["rangier_m"] + 1.0, 18.0), 1)


def imagery_date(lat, lon, client):
    """Aufnahmedatum der Esri-Kachel (nur fuer Esri bekannt), sonst ''."""
    try:
        r = client.get("https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/identify",
                       params={"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "sr": 4326, "layers": "all",
                               "tolerance": 2, "mapExtent": f"{lon - .01},{lat - .01},{lon + .01},{lat + .01}",
                               "imageDisplay": "400,400,96", "returnGeometry": "false", "f": "json"})
        for res in r.json().get("results", []):
            v = (res.get("attributes") or {}).get("DATE (YYYYMMDD)", "")
            if re.fullmatch(r"\d{8}", v or ""):
                return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    except (httpx.HTTPError, ValueError):
        pass
    return ""


# ====================================================================== OSM vor Ort (Overpass)
def osm_site(osm, lat, lon):
    """Umriss des Platzes, Stellflaechen (camp_pitch/parking_space) und Strassen der Zufahrt mit Beschraenkungen."""
    import overpass
    typ, _, oid = osm.partition("/")
    own = f"{typ}({oid});" if typ in ("node", "way", "relation") else ""   # „out“ erst am Ende – in der Union waere es ein Fehler (400)
    q = (f"[out:json][timeout:60];({own}"
         f"nwr(around:300,{lat},{lon})[tourism=camp_pitch];"
         f"way(around:300,{lat},{lon})[amenity=parking_space];"
         f"way(around:400,{lat},{lon})[highway][~\"^(maxlength|maxwidth|narrow|width|hgv|motorhome|trailer|caravan)$\"~\".\"];"
         ");out geom 400;")
    try:
        data = overpass.query(q, timeout=90)
    except Exception as e:   # noqa: BLE001 – Overpass ist Beiwerk: ohne Umriss geht es mit dem Punkt weiter
        log.info("Overpass %s: %s", osm, e)
        return {"outline": None, "pitches": [], "roads": [], "tags": None, "ok": False}
    outline, pitches, roads, own_tags = None, [], [], None
    for el in data.get("elements", []):
        tg = el.get("tags") or {}
        geom = [(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p]
        if f"{el.get('type')}/{el.get('id')}" == osm:
            own_tags = tg
        if el.get("type") == "relation" and f"relation/{el.get('id')}" == osm:
            ring = []
            for m in el.get("members", []):
                if m.get("role") == "outer":
                    ring += [(p["lat"], p["lon"]) for p in m.get("geometry") or []]
            outline = ring or None
        elif f"{el.get('type')}/{el.get('id')}" == osm:
            outline = geom if el.get("type") == "way" else None
        elif tg.get("tourism") == "camp_pitch" or tg.get("amenity") == "parking_space":
            if len(geom) >= 4:
                pitches.append(geom)
        elif tg.get("highway"):
            roads.append({"tags": tg, "geom": geom})
    return {"outline": outline, "pitches": pitches, "roads": roads, "tags": own_tags, "ok": True}


def _local(pts, lat0):
    k = 111320.0
    return [((lo) * k * math.cos(math.radians(lat0)), la * k) for la, lo in pts]


def rect_dims(pts):
    """Kleinstes umschliessendes Rechteck -> (lang, kurz) in m."""
    from shapely.geometry import MultiPoint
    if len(pts) < 3:
        return 0.0, 0.0
    lat0 = sum(p[0] for p in pts) / len(pts)
    r = MultiPoint(_local(pts, lat0)).minimum_rotated_rectangle
    c = list(r.exterior.coords) if r.geom_type == "Polygon" else []
    if len(c) < 4:
        return 0.0, 0.0
    a, b = math.dist(c[0], c[1]), math.dist(c[1], c[2])
    return max(a, b), min(a, b)


def geometry_facts(site):
    out = {}
    if site.get("outline"):
        lng, srt = rect_dims(site["outline"])
        out["platz_m"] = [round(lng), round(srt)]
    dims = sorted((rect_dims(p) for p in site.get("pitches") or []), reverse=True)
    dims = [d for d in dims if 3 <= d[0] <= 40 and d[1] >= 2]
    if dims:
        out["stellflaechen"] = len(dims)
        out["stellflaeche_max_m"] = round(dims[0][0], 1)
        out["stellflaeche_median_m"] = round(dims[len(dims) // 2][0], 1)
    return out


def _measure(v):
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*(m|ft|')?\s*$", str(v or ""))
    if not m:
        return None
    x = float(m.group(1).replace(",", "."))
    return round(x * 0.3048, 1) if m.group(2) in ("ft", "'") else x


def _near(geom, lat, lon):
    return min((math.dist(_local([p], lat)[0], _local([(lat, lon)], lat)[0]) for p in geom), default=9e9)


def access_hints(site, lat, lon):
    """Beschraenkungen auf Strassen bis 400 m um den Platz -> [{"was", "wert", "abstand"}] (naechste zuerst)."""
    out = []
    for r in site.get("roads") or []:
        tg, dist = r["tags"], round(_near(r["geom"], lat, lon))
        if (ml := _measure(tg.get("maxlength"))) and MIN_M <= ml <= MAX_M:
            out.append({"was": "Längenbeschränkung", "wert": ml, "abstand": dist})
        if (mw := _measure(tg.get("maxwidth"))) and mw < 3.0:
            out.append({"was": "max. Breite", "wert": mw, "abstand": dist})
        if (w := _measure(tg.get("width"))) and w < 3.5 and tg.get("highway") not in ("footway", "path", "cycleway", "track"):
            out.append({"was": "schmale Straße", "wert": w, "abstand": dist})
        if tg.get("narrow") == "yes":
            out.append({"was": "Engstelle", "wert": None, "abstand": dist})
        for k in ("trailer", "caravan", "motorhome", "hgv"):
            if tg.get(k) == "no":
                out.append({"was": {"trailer": "Anhänger verboten", "caravan": "Wohnwagen verboten",
                                    "motorhome": "Wohnmobile verboten", "hgv": "Lkw verboten"}[k], "wert": None, "abstand": dist})
    out.sort(key=lambda h: h["abstand"])
    return out[:6]


# ====================================================================== Gesamturteil
def verdict(res):
    """Bausteine -> {"m", "kind", "level", "src", "why"} oder None.
    level confirmed = steht so im Text; likely = aus Luftbild/OSM-Massen geschaetzt."""
    txt = res.get("text") or {}
    if txt.get("m"):
        src = {"osm": "Beschreibung in OSM", "osmtag": "OpenStreetMap", "fr": "Tourismusamt", "web": "Website"}.get(txt.get("src"), "Text")
        return {"m": txt["m"], "kind": txt["kind"], "level": "confirmed", "src": src, "why": txt.get("quote", "")}
    geo = res.get("geo") or {}
    lb = res.get("luft") or {}
    parts, why = [], []
    if geo.get("stellflaechen", 0) >= 3 and geo.get("stellflaeche_max_m", 0) >= MIN_M:
        parts.append(geo["stellflaeche_max_m"])
        why.append(f"{geo['stellflaechen']} Stellflächen in OSM, längste {_de(geo['stellflaeche_max_m'])} m")
    if image_estimate(lb) is not None:
        if not parts:
            parts.append(lb["stellbahn_m"] - 1.0)
            why.append(f"freie Bahn bis {_de(lb['stellbahn_m'])} m")
        parts.append(min(lb["rangier_m"] + 1.0, 18.0))
        why.append(f"Rangierfläche {_de(lb['rangier_m'])} × {_de(lb['rangier_m'])} m")
    if not parts:
        return None
    est, src, why = min(parts), ("Luftbild-Messung" if lb else "Stellflächen in OSM"), ", ".join(why)
    caps = [h for h in res.get("zufahrt") or [] if h["was"] == "Längenbeschränkung" and h["abstand"] <= 150 and h["wert"] < est]
    if caps:
        est, why = caps[0]["wert"], f"{why} – Zufahrt mit Längenbeschränkung {caps[0]['wert']} m".strip(" –")
    est = round(float(est), 1)
    return {"m": est, "kind": "min" if est >= 18 else "max", "level": "likely", "src": src, "why": why}


def _de(v):
    return f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def public(key, res):
    v = res.get("verdict")
    out = {"state": "done", "verdict": v, "trailer": (res.get("text") or {}).get("trailer"),
           "zufahrt": res.get("zufahrt") or [], "geo": res.get("geo") or {}, "at": res.get("at", "")}
    lb = res.get("luft")
    if lb:
        out["luft"] = {k: lb.get(k) for k in ("stellbahn_m", "rangier_m", "offen", "flaeche_m2", "src", "date")}
        out["luft"]["img"] = bool(lb.get("img"))
    if (res.get("text") or {}).get("url"):
        out["url"] = res["text"]["url"]
    return out


# ====================================================================== Ablauf je Platz
def analyze(place, lookup=None):
    """place: {key, lat, lon, tags (OSM), fr (DATAtourisme-Eintrag oder None)} -> Ergebnis (gespeichert)."""
    import frcamps
    key, lat, lon = place["key"], place["lat"], place["lon"]
    tags, fr = place.get("tags") or {}, place.get("fr")
    res = {"at": time.strftime("%Y-%m-%d")}
    # OSM vor Ort zuerst: liefert Umriss, Stellflaechen, Zufahrt – und die Merkmale des Platzes selbst (der
    # Auswerte-Prozess laedt die grosse OSM-DB bewusst nicht, 640 MB)
    site = osm_site(key, lat, lon) if key.startswith(("node/", "way/", "relation/")) else {"outline": None, "pitches": [], "roads": [], "ok": True}
    if site.get("ok") is False:
        raise RuntimeError("Overpass nicht erreichbar")
    tags = tags or site.get("tags") or {}
    if fr is None and not key.startswith("dt:") and frcamps.in_france(lat, lon):
        fr = frcamps.lookup([(key, lat, lon, tags.get("name", ""))], []).get("match", {}).get(key)
    fr = fr or {}
    if str(tags.get("maxlength", "")).strip().lower() in ("none", "unlimited", "no"):   # ausdruecklich ohne Begrenzung
        res["text"] = {"kind": "none", "m": NO_LIMIT, "quote": "maxlength=none (ohne Längenbegrenzung)", "src": "osmtag"}
    # 1. Text: OSM, Tourismusamt, Website
    texts = [("osm", " ".join(filter(None, (tags.get("description:de"), tags.get("description"), tags.get("note"))))),
             ("fr", fr.get("d", ""))]
    for src, t in texts:
        if res.get("text"):
            break
        if t and (hit := from_text(t)):
            res["text"] = {**hit, "src": src}
            break
    web = tags.get("website") or tags.get("contact:website") or fr.get("w") or ""
    if not (res.get("text") or {}).get("m") and web.startswith(("http://", "https://")):
        for url, t in website_text(web):
            if hit := from_text(t):
                if hit.get("m"):
                    res["text"] = {**hit, "src": "web", "url": url}
                    break
                res.setdefault("text", {**hit, "src": "web", "url": url})
    # 2. OSM vor Ort + Luftbild (nur, wenn der Text nichts Genaues sagt)
    res["geo"] = geometry_facts(site)
    res["zufahrt"] = access_hints(site, lat, lon)
    if not (res.get("text") or {}).get("m") and site["outline"] and len(site["outline"]) >= 4:
        pts = site["outline"]
        s, n = min(p[0] for p in pts), max(p[0] for p in pts)
        w, e = min(p[1] for p in pts), max(p[1] for p in pts)
        pad_lat = max(25, 0.15 * (n - s) * 111320) / 111320
        pad_lon = pad_lat / math.cos(math.radians(lat))
        with httpx.Client(timeout=15, headers={"User-Agent": UA}) as c:
            shot = aerial(lat, lon, (s - pad_lat, n + pad_lat, w - pad_lon, e + pad_lon), client=c)
            date = imagery_date(lat, lon, c) if shot and shot["src"]["id"] == "esri" else ""
        if shot:
            m, jpeg = measure(shot, pts, site["pitches"])
            os.makedirs(IMG_DIR, exist_ok=True)
            fn = re.sub(r"[^\w.-]", "_", key) + ".jpg"
            with open(os.path.join(IMG_DIR, fn), "wb") as f:
                f.write(jpeg)
            res["luft"] = {**m, "src": shot["src"]["name"], "date": date, "img": fn, "m_px": round(shot["mpp"], 3)}
    res["verdict"] = verdict(res)
    _put(key, res)
    return res


# ====================================================================== Abfrage / Warteschlange
def lookup(places, prio=0.0):
    """places: [{key, lat, lon, tags, fr}] -> {key: public | {"state": "pending"}}; Fehlende kommen in die Warteschlange."""
    out, todo = {}, []
    for p in places:
        hit = _get(p["key"])
        if hit is not None:
            out[p["key"]] = public(p["key"], hit)
        else:
            out[p["key"]] = {"state": "pending"}
            todo.append(p)
    if todo:
        now = time.time()
        with _lock, _db() as con:
            n = con.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
            for i, p in enumerate(todo):
                if n >= MAX_QUEUE:
                    out[p["key"]] = {"state": "none"}
                    continue
                cur = con.execute("INSERT OR IGNORE INTO queue VALUES (?, ?, ?, ?)",
                                  (p["key"], prio + i / 1000, now, json.dumps(p, ensure_ascii=False)))
                n += cur.rowcount
            con.execute("UPDATE queue SET prio = MIN(prio, ?) WHERE key IN ({})".format(",".join("?" * len(todo))),  # noqa: S608 – nur Platzhalter
                        (prio, *[p["key"] for p in todo]))
        start()
    return out


def night_verdict(key, lat, lon, tags):
    """Fuer die Nachtplatz-Vorschlaege: gespeichertes Urteil oder None – fehlt es, kommt der Platz vorn in die
    Warteschlange (Vorschlaege sind die wichtigsten Plaetze einer Route)."""
    try:
        hit = _get(key)
        if hit is None and valid_key(key):
            lookup([{"key": key, "lat": float(lat), "lon": float(lon), "tags": tags, "fr": None}], prio=-1.0)
    except sqlite3.Error:
        return None
    return (hit or {}).get("verdict")


def cached_verdict(key):
    try:
        return (_get(key) or {}).get("verdict")
    except sqlite3.Error:
        return None


def status():
    with _lock:
        q = _db().execute("SELECT COUNT(*) FROM queue").fetchone()[0]
    return {"luftbild": True, "queue": q}


def image_path(key):
    if not valid_key(key):
        return None
    hit = _get(key)
    fn = ((hit or {}).get("luft") or {}).get("img")
    path = os.path.join(IMG_DIR, fn) if fn else ""
    return path if fn and os.path.isfile(path) else None


def _next():
    with _lock:
        row = _db().execute("SELECT key, place FROM queue ORDER BY prio, at LIMIT 1").fetchone()
    return (row[0], json.loads(row[1])) if row else (None, None)


def resolve(place):
    """DATAtourisme-Plaetze (dt:…) aus der eigenen Liste ergaenzen; OSM-Plaetze holen ihre Merkmale in analyze()
    per Overpass. None, wenn ein dt:-Platz nicht (mehr) in der Liste steht."""
    if not place["key"].startswith("dt:") or place.get("fr"):
        return place
    import frcamps
    la, lo = float(place["lat"]), float(place["lon"])
    fr = next((it for _, it in frcamps._near(la, lo, 200) if it["id"] == place["key"][3:]), None)
    return {**place, "tags": {}, "fr": fr} if fr else None


def run_worker(idle=5.0, once=False):
    """Auswerte-Schleife im EIGENEN Prozess (`python -m platzlaenge`): Overpass, Websites, Kacheln und Bildmessung
    halten so nie das GIL der Web-App fest (dieselbe Lehre wie DATAtourisme 0.38.4). Nur ein Auswerter je Daten-
    verzeichnis (Sperrdatei); die Web-App reiht nur ein und liest Ergebnisse."""
    import fcntl
    os.makedirs(DATA, exist_ok=True)
    lockf = open(os.path.join(DATA, "platzlaenge.lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.info("Auswerter laeuft schon")
        return
    while True:
        key, place = _next()
        if not key:
            if once:
                return
            time.sleep(idle)
            continue
        try:
            full = resolve(place)
            if full is None:
                _put(key, {"at": time.strftime("%Y-%m-%d"), "verdict": None, "unbekannt": True})
            else:
                analyze(full)
        except Exception as e:   # noqa: BLE001 – ein Platz darf die Schlange nie anhalten
            log.warning("Platz %s: %s", key, e)
            _put(key, {"at": time.strftime("%Y-%m-%d"), "error": type(e).__name__, "verdict": None}, ok=False)
            time.sleep(30)
        if once:
            continue
        time.sleep(2)


def _supervise():
    """Kindprozess starten und bei Absturz nach einer Pause neu starten (in der Web-App nur dieser Thread)."""
    import subprocess
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        t0 = time.time()
        try:
            subprocess.run([sys.executable, "-m", "platzlaenge"], cwd=here, env={**os.environ, "DATA_DIR": DATA}, check=False)
        except OSError as e:
            log.warning("Auswerter nicht gestartet: %s", e)
        time.sleep(60 if time.time() - t0 < 300 else 5)


def start():
    with _lock:
        if _state["started"] or os.environ.get("PLATZLAENGE_WORKER", "1") != "1":
            return
        _state["started"] = True
    threading.Thread(target=_supervise, daemon=True, name="platzlaenge").start()


if __name__ == "__main__":   # Kindprozess von _supervise()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.nice(15)
    run_worker()
