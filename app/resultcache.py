"""Zwischenspeicher fertiger Analysen (seit 0.46.0): dieselbe GPX mit denselben Optionen und demselben Daten-/Codestand
wird nicht noch einmal gerechnet. Nur vollstaendig lokale Ergebnisse (ohne "missing"), 7 Tage, hoechstens 500 MB.

Ablage DATA_DIR/result-cache/<key>.json.gz; Fehler beim Lesen/Schreiben werden nur geloggt."""

import gzip
import hashlib
import json
import logging
import os
import time

log = logging.getLogger("resultcache")

CACHE_DAYS = 7
CACHE_MAX_BYTES = 500 * 1024 ** 2


def _dir():
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "result-cache")


def key(raw, options, stamp):
    h = hashlib.sha1(raw, usedforsecurity=False)
    h.update(b"|" + json.dumps(options or {}, sort_keys=True).encode() + b"|" + str(stamp).encode())
    return h.hexdigest()[:24]


def _file(k):
    return os.path.join(_dir(), k + ".json.gz")


def load(k, now=None):
    now = now or time.time()
    path = _file(k)
    try:
        if now - os.path.getmtime(path) > CACHE_DAYS * 86400:
            return None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001 – auch zlib.error: kaputte Datei ist ein Fehltreffer
        log.warning("Zwischenspeicher %s unlesbar, verworfen: %s", k, e)
        try:
            os.remove(path)
        except OSError:
            pass
        return None


def complete(res):
    """Nur wirklich vollstaendige Ergebnisse aufheben (Review 12.11.2026): ganz lokal gerechnet, keine
    voruebergehend fehlenden Daten (Landschaft/Kartenserver, Einkaufsdaten, Hoehenprofil)."""
    if not isinstance(res, dict) or res.get("missing"):
        return False
    if res.get("trip"):
        return bool(res.get("stages")) and all(complete(s) for s in res["stages"])
    if (res.get("stats") or {}).get("source") != "lokal":
        return False
    if any("Kartenserver" in str(w) for w in res.get("warnings") or []):
        return False
    if any(n.get("label") == "Einkaufsdaten fehlen" for n in res.get("notes") or [] if isinstance(n, dict)):
        return False
    rig = res.get("rig")
    return not (isinstance(rig, dict) and rig.get("elevation") is False)


def store(k, result, now=None):
    if not isinstance(result, dict) or result.get("missing"):
        return False
    path = _file(k)
    try:
        os.makedirs(_dir(), exist_ok=True)
        with gzip.open(path + ".part", "wt", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        os.replace(path + ".part", path)
    except OSError as e:
        log.warning("Zwischenspeicher %s nicht geschrieben: %s", k, e)
        return False
    tidy(now)
    return True


def tidy(now=None):
    now = now or time.time()
    try:
        files = []
        for name in os.listdir(_dir()):
            p = os.path.join(_dir(), name)
            st = os.stat(p)
            if now - st.st_mtime > CACHE_DAYS * 86400 or name.endswith(".part") and now - st.st_mtime > 3600:
                os.remove(p)
            elif name.endswith(".json.gz"):
                files.append((st.st_mtime, st.st_size, p))
        total = sum(s for _, s, _ in files)
        for _, size, p in sorted(files):
            if total <= CACHE_MAX_BYTES:
                break
            os.remove(p)
            total -= size
    except OSError as e:
        log.warning("Zwischenspeicher aufraeumen: %s", e)
