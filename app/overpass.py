"""Overpass-Client: Datei-Cache (gzip, TTL), Fallback-Server, hoechstens eine
Anfrage gleichzeitig (Fair-Use der oeffentlichen Instanzen)."""

import gzip
import hashlib
import json
import logging
import os
import threading
import time

import httpx

log = logging.getLogger("overpass")

ENDPOINTS = [u.strip() for u in os.environ.get(
    "OVERPASS_URLS",
    "https://overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter,"
    "https://overpass.private.coffee/api/interpreter",
).split(",") if u.strip()]
CACHE_DIR = os.environ.get("CACHE_DIR", "/data/cache")
CACHE_TTL = int(os.environ.get("CACHE_TTL_DAYS", "14")) * 86400
USER_AGENT = "RideAnalyzer-Prototyp/0.1 (+https://rideanalyzer.petrick.top)"

_lock = threading.Semaphore(1)


class OverpassError(RuntimeError):
    pass


def _cache_path(q: str) -> str:
    key = hashlib.sha256(q.encode()).hexdigest()
    return os.path.join(CACHE_DIR, key[:2], key + ".json.gz")


def cached(q: str):
    path = _cache_path(q)
    try:
        if time.time() - os.path.getmtime(path) < CACHE_TTL:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)
    except (FileNotFoundError, OSError, ValueError):
        pass
    return None


def query(q: str, timeout: float = 150, require: bool = False, notify=lambda m: None) -> dict:
    """require=True: leere Antwort gilt als Fehler (Overpass liefert unter Last
    gelegentlich 200 mit leerer Liste). Leere Antworten werden nie gecacht.
    Der Hauptserver bekommt Backoff (10/20/40 s), die Ausweichserver je einen Versuch."""
    hit = cached(q)
    if hit is not None:
        return hit
    path = _cache_path(q)
    last = "keine Antwort"
    with _lock, httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}) as c:
        for n, url in enumerate(ENDPOINTS):
            tries = 4 if n == 0 else 1
            for attempt in range(tries):
                try:
                    r = c.post(url, data={"data": q})
                except httpx.HTTPError as e:
                    last = f"{url}: {e.__class__.__name__}"
                    log.warning("Overpass %s", last)
                    break
                if r.status_code in (429, 502, 503, 504):
                    last = f"{url}: HTTP {r.status_code}"
                    log.warning("Overpass %s, Versuch %d", last, attempt + 1)
                    if attempt + 1 < tries:
                        wait = 10 * 2 ** attempt
                        notify(f"Kartenserver ausgelastet – neuer Versuch in {wait} s")
                        time.sleep(wait)
                    continue
                if r.status_code != 200:
                    last = f"{url}: HTTP {r.status_code}"
                    log.warning("Overpass %s", last)
                    break
                try:
                    data = r.json()
                except ValueError:
                    last = f"{url}: keine JSON-Antwort"
                    log.warning("Overpass %s", last)
                    break
                remark = data.get("remark", "")
                if "runtime error" in remark or "Query timed out" in remark:
                    last = f"{url}: {remark[:120]}"
                    log.warning("Overpass %s", last)
                    break
                if not data.get("elements"):
                    if require:
                        last = f"{url}: leere Antwort"
                        log.warning("Overpass %s", last)
                        break
                    return data
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    tmp = path + ".tmp"
                    with gzip.open(tmp, "wt", encoding="utf-8") as f:
                        json.dump(data, f)
                    os.replace(tmp, path)
                except OSError as e:
                    log.warning("Cache nicht schreibbar: %s", e)
                return data
            if n + 1 < len(ENDPOINTS):
                notify("Hauptserver überlastet – versuche Ausweichserver")
    raise OverpassError(f"Kartendaten gerade nicht abrufbar ({last}). Bitte in ein paar Minuten erneut versuchen – "
                        "bereits geladene Teilstücke bleiben gespeichert.")
