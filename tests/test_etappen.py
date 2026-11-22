"""Lange Einzelstrecke in Etappen teilen (0.47.0, Ticket #5)."""
import pytest

import etappen


def line(km_total, n=2000, lat=45.0, lon0=-1.0):
    """Gerade Linie nach Osten, ~km_total lang, n Punkte."""
    import math
    dlon = km_total / (111.32 * math.cos(math.radians(lat)))
    return [(lat, lon0 + dlon * i / (n - 1), None) for i in range(n)]


def test_track_km_straight():
    km = etappen.track_km(line(1858))
    assert km[-1] == pytest.approx(1858, rel=0.01)


@pytest.mark.parametrize("total, want", [(1858, 4), (1001, 3), (1500, 3), (2400, 5)])
def test_parts_for(total, want):
    assert etappen.parts_for(total, 1000) == want


def test_split_without_places_even_and_under_limit():
    pts = line(1858)
    stages, info = etappen.split_long(pts, 1000)
    assert info["parts"] == 4 and info["km"] == pytest.approx(1858, rel=0.01)
    assert info["stage_km"] < 1000
    assert [s[0] for s in stages] == ["Etappe 1", "Etappe 2", "Etappe 3", "Etappe 4"]
    assert all(s[1] is None for s in stages)
    for a, b in zip(stages, stages[1:]):          # Nachbarn teilen den Schnittpunkt
        assert a[2][-1] == b[2][0]
    assert stages[0][2][0] == pts[0] and stages[-1][2][-1] == pts[-1]


def test_split_snaps_to_town_in_window():
    pts = line(1858)
    km = etappen.track_km(pts)
    town_km = 1858 / 4 + 40            # 40 km hinter dem idealen Schnitt, innerhalb von 15 %
    i_town = int(abs(km - town_km).argmin())
    tlon = pts[i_town][1]

    def places(lat, lon, r):
        d = abs(lon - tlon) * 78_700
        return [("Toulouse", "city", d)] if d <= r else []
    stages, _ = etappen.split_long(pts, 1000, places=places)
    assert stages[0][0] == "Etappe 1: Start → Toulouse" and stages[1][0].startswith("Etappe 2: Toulouse → ")
    assert abs(etappen.track_km(stages[0][2])[-1] - town_km) < 6   # Schnitt am Ort (Stichprobe alle 5 km)


def test_hamlet_and_unnamed_are_ignored():
    pts = line(1200)
    stages, _ = etappen.split_long(pts, 1000, places=lambda la, lo, r: [("Weiler", "hamlet", 10), ("", "town", 10)])
    assert stages[0][0] == "Etappe 1"


def _gpx(pts):
    rows = "".join(f'<trkpt lat="{la:.6f}" lon="{lo:.6f}"/>' for la, lo, _ in pts)
    return f'<?xml version="1.0"?><gpx version="1.1"><trk><name>Berlin-Barcelona</name><trkseg>{rows}</trkseg></trk></gpx>'.encode()


def test_main_split_long_only_over_limit(monkeypatch):
    import main
    import osmdb
    monkeypatch.setattr(osmdb, "get", lambda: None)
    stages, name, info = main._split_long(_gpx(line(1858, n=600)))
    assert len(stages) == 4 and name == "Berlin-Barcelona" and info["parts"] == 4
    assert main._split_long(_gpx(line(800, n=300))) == ([], "", None)
