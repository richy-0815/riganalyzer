"""0.46.0: Zwischenspeicher fertiger Analysen (7 Tage, 500 MB, nur vollständig lokale)."""
import os
import time

import resultcache


def _dir():
    return os.path.join(os.environ["DATA_DIR"], "result-cache")


def test_treffer_und_schluessel(data_dir):
    k = resultcache.key(b"g", {"rig": True}, "s1")
    assert resultcache.store(k, {"stats": {"km": 1}})
    assert resultcache.load(k) == {"stats": {"km": 1}}
    assert resultcache.load(resultcache.key(b"g", {"rig": False}, "s1")) is None
    assert resultcache.load(resultcache.key(b"g", {"rig": True}, "s2")) is None
    assert resultcache.load(resultcache.key(b"h", {"rig": True}, "s1")) is None


def test_missing_wird_nicht_gespeichert(data_dir):
    k = resultcache.key(b"g", {}, "s")
    assert resultcache.store(k, {"missing": [{"region": "x"}]}) is False
    assert resultcache.load(k) is None


def test_ablauf_7_tage(data_dir):
    k = resultcache.key(b"g", {}, "s")
    resultcache.store(k, {"a": 1})
    old = time.time() - 8 * 86400
    for f in os.listdir(_dir()):
        os.utime(os.path.join(_dir(), f), (old, old))
    assert resultcache.load(k) is None


def test_platzgrenze(data_dir, monkeypatch):
    big = {"x": os.urandom(20000).hex()}
    keys = [resultcache.key(bytes([i]), {}, "s") for i in range(3)]
    resultcache.store(keys[0], big)
    size = os.path.getsize(os.path.join(_dir(), os.listdir(_dir())[0]))
    monkeypatch.setattr(resultcache, "CACHE_MAX_BYTES", int(size * 2.5))
    t = time.time()
    os.utime(os.path.join(_dir(), keys[0] + ".json.gz"), (t - 30, t - 30))
    resultcache.store(keys[1], big)
    os.utime(os.path.join(_dir(), keys[1] + ".json.gz"), (t - 20, t - 20))
    resultcache.store(keys[2], big)
    assert resultcache.load(keys[0]) is None
    assert resultcache.load(keys[1]) == big and resultcache.load(keys[2]) == big


def test_kaputte_datei(data_dir):
    k = resultcache.key(b"g", {}, "s")
    os.makedirs(_dir(), exist_ok=True)
    path = os.path.join(_dir(), k + ".json.gz")
    with open(path, "wb") as f:
        f.write(b"kein gzip")
    assert resultcache.load(k) is None
    assert not os.path.exists(path)


def test_nicht_beschreibbar(data_dir, monkeypatch, tmp_path):
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    monkeypatch.setenv("DATA_DIR", str(blocker))
    assert resultcache.store(resultcache.key(b"g", {}, "s"), {"a": 1}) is False


# ---------------------------------------------------------------- Randfaelle
def test_kaputter_gzip_strom_ist_fehltreffer(data_dir):
    # zlib.error (keine OSError/ValueError) darf nicht durchschlagen
    import gzip
    k = resultcache.key(b"g", {}, "s")
    os.makedirs(_dir(), exist_ok=True)
    path = os.path.join(_dir(), k + ".json.gz")
    good = gzip.compress(b'{"a": 1}' * 200)
    with open(path, "wb") as f:
        f.write(good[:10] + b"\x00" * 30 + good[40:])
    assert resultcache.load(k) is None
    assert not os.path.exists(path)


def test_vollstaendig():
    # nur wirklich vollständige Ergebnisse taugen für den Zwischenspeicher
    ok = {"stats": {"source": "lokal"}, "warnings": [], "notes": [], "rig": {"elevation": True}}
    assert resultcache.complete(ok)
    assert not resultcache.complete({**ok, "missing": [{"region": "x"}]})
    assert not resultcache.complete({**ok, "stats": {"source": "gemischt (1/2 lokal)"}})
    assert not resultcache.complete({**ok, "warnings": ["Landschaftsdaten fehlen für 1 von 2 Teilstücken (Kartenserver ausgelastet)."]})
    assert not resultcache.complete({**ok, "notes": [{"t": "shop", "label": "Einkaufsdaten fehlen"}]})
    assert resultcache.complete({**ok, "notes": [{"t": "shop", "label": "Einkaufen: bisher nur Deutschland"}]})
    assert not resultcache.complete({**ok, "rig": {"elevation": False}})
    assert resultcache.complete({"trip": True, "stages": [ok, ok]})
    assert not resultcache.complete({"trip": True, "stages": [ok, {**ok, "stats": {"source": "Overpass"}}]})
