"""Duenne GPX (ein Punkt alle ~5 km, z. B. Schottland-Reise): Luecken ueber das echte Strassennetz schliessen
statt Luftlinie – und dort normal pruefen (seit v0.13.2)."""
import os
import shutil

import analyze
import build_db
import osmdb

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _db(tmp_path, monkeypatch):
    def fake_download(region, workdir):
        os.makedirs(workdir, exist_ok=True)
        shutil.copy(os.path.join(FIX, "gap.osm"), os.path.join(workdir, "gap.osm"))
        return os.path.join(workdir, "gap.osm"), os.path.join(FIX, "gap.poly")
    monkeypatch.setattr(build_db, "download", fake_download)
    dst = str(tmp_path / "gap.sqlite")
    build_db.build(dst, ["europe/test"], str(tmp_path / "work"))
    s = osmdb.OsmDBSet([osmdb.OsmDB(dst)])
    monkeypatch.setattr(osmdb, "get", lambda: s)
    return s


def test_gap_follows_real_road(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    pts = [(43.8000, 6.4500, None), (43.8000, 6.5100, None)]          # 4,8 km Luftlinie, Strasse macht 2-km-Bogen
    out, info = analyze._reconstruct(pts, lambda p, m: None)
    assert max(p[0] for p in out) > 43.815                              # folgt dem Bogen nach Norden
    assert info["gaps"] == [] and info["routed"] == 1                   # Luecke geschlossen -> wird geprueft
    assert info["gaps_routed"] == 1


def test_far_gap_stays_unknown(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    pts = [(43.8000, 6.4500, None), (43.8000, 6.5100, None), (43.80, 7.20, None)]   # zweite Luecke ~55 km: Faehre o. ae.
    out, info = analyze._reconstruct(pts, lambda p, m: None)
    assert len(info["gaps"]) == 1
