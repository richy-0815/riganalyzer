"""DEM_SHARED_DIR (0.46.1): QA liest Prods Kacheln nur und schreibt nie dorthin."""
import os

import dem


class _R:
    status_code = 404


def _setup(tmp_path, monkeypatch):
    own, shared = tmp_path / "own", tmp_path / "shared"
    own.mkdir()
    shared.mkdir()
    monkeypatch.setattr(dem, "DEM_DIR", str(own))
    monkeypatch.setattr(dem, "DEM_SHARED_DIR", str(shared))
    dem._cache.clear()
    return own, shared


def test_shared_marker_used_without_download(tmp_path, monkeypatch):
    own, shared = _setup(tmp_path, monkeypatch)
    (shared / (dem._name(10, 10) + ".tif.none")).touch()
    monkeypatch.setattr(dem.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein Download")))
    assert dem._tile(10, 10) is None
    assert os.listdir(own) == []


def test_missing_everywhere_downloads_into_own_dir(tmp_path, monkeypatch):
    own, shared = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(dem.httpx, "get", lambda *a, **k: _R())
    assert dem._tile(11, 11) is None
    assert (own / (dem._name(11, 11) + ".tif.none")).exists()
    assert os.listdir(shared) == []
