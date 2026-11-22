"""Garmin-Routen (rtept + gpxx:rpt): die berechnete Strecke steckt in den rpt-Zwischenpunkten."""
import analyze

GARMIN = b"""<?xml version="1.0" encoding="utf-8"?>
<gpx version="1.1" creator="Garmin Desktop App" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxx="http://www.garmin.com/xmlschemas/GpxExtensions/v3">
<rte><name>Alle Kurven</name>
 <rtept lat="49.0" lon="8.0"><name>A</name><extensions><gpxx:RoutePointExtension>
   <gpxx:Subclass>000</gpxx:Subclass>
   <gpxx:rpt lat="49.0" lon="8.0"/>
   <gpxx:rpt lat="49.001" lon="8.002"/>
   <gpxx:rpt lat="49.002" lon="8.001"/>
 </gpxx:RoutePointExtension></extensions></rtept>
 <rtept lat="49.003" lon="8.003"><name>B</name><extensions><gpxx:RoutePointExtension>
   <gpxx:rpt lat="49.003" lon="8.003"/>
   <gpxx:rpt lat="49.004" lon="8.005"/>
 </gpxx:RoutePointExtension></extensions></rtept>
 <rtept lat="49.005" lon="8.006"><name>C</name></rtept>
</rte></gpx>"""

PLAIN = b"""<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
<rte><rtept lat="49.0" lon="8.0"/><rtept lat="49.1" lon="8.1"/></rte></gpx>"""


def test_parse_gpx_expands_garmin_shape():
    pts, name = analyze.parse_gpx(GARMIN)
    assert name == "Alle Kurven"
    assert [(la, lo) for la, lo, _ in pts] == [
        (49.0, 8.0), (49.001, 8.002), (49.002, 8.001),
        (49.003, 8.003), (49.004, 8.005), (49.005, 8.006)]


def test_parse_stages_expands_garmin_shape():
    # zwei Routen = zwei Etappen, jede mit ihren Zwischenpunkten
    two =GARMIN.replace(b"</gpx>", b"") + GARMIN[GARMIN.index(b"<rte>"):]
    stages, _ = analyze.parse_stages(two)
    assert len(stages) == 2
    assert all(len(s[2]) == 6 for s in stages)


def test_plain_route_unchanged():
    pts, _ = analyze.parse_gpx(PLAIN)
    assert [(la, lo) for la, lo, _ in pts] == [(49.0, 8.0), (49.1, 8.1)]
