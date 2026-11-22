"""0.46.0: Warteschlange – km aus GPX, Schätzer, Spur-Wahl, Wartetext."""
import jobqueue

BERLIN, POTSDAM, COTTBUS = (52.52, 13.405), (52.39, 13.065), (51.76, 14.33)


def gpx(*tracks):
    body = "".join("<trk><trkseg>" + "".join(f'<trkpt lat="{a}" lon="{b}"/>' for a, b in t) + "</trkseg></trk>" for t in tracks)
    return f'<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">{body}</gpx>'.encode()


def test_route_km_strecke():
    assert 23 < jobqueue.route_km(gpx([BERLIN, POTSDAM])) < 29


def test_route_km_reise_und_kaputt():
    one = jobqueue.route_km(gpx([BERLIN, POTSDAM]))
    two = jobqueue.route_km(gpx([BERLIN, POTSDAM], [POTSDAM, COTTBUS]))
    assert two > one + 100
    assert jobqueue.route_km(b"kaputt") == 0.0


def test_rate():
    r = jobqueue.Rate()
    assert r.value() == 0.15 and r.est(10) == 3
    r.add(100, 20)
    r.add(200, 20)
    r.add(100, 30)
    assert r.value() == 0.2
    r.add(0.5, 99)
    assert r.value() == 0.2
    for _ in range(20):
        r.add(100, 50)
    assert r.value() == 0.5


def test_lane_for():
    assert jobqueue.lane_for(299, False, 300, 1) == "kurz"
    assert jobqueue.lane_for(300, False, 300, 1) == "kurz"
    assert jobqueue.lane_for(301, False, 300, 1) == "lokal"
    assert jobqueue.lane_for(10, True, 300, 1) == "live"
    assert jobqueue.lane_for(10, False, 300, 0) == "lokal"


def _jobs():
    return {"run": {"state": "running", "lane": "lokal", "seq": 1, "est": 240, "progress": 50},
            "a": {"state": "queued", "lane": "lokal", "seq": 2, "est": 60, "progress": 0},
            "b": {"state": "queued", "lane": "lokal", "seq": 3, "est": 60, "progress": 0},
            "k": {"state": "queued", "lane": "kurz", "seq": 4, "est": 10, "progress": 0}}


SLOTS = {"lokal": 1, "kurz": 1, "live": 1}


def test_wait_text():
    j = _jobs()
    assert jobqueue.wait_text(j, "b", SLOTS) == "Platz 2 in der Warteschlange · noch ca. 3 Min."
    assert jobqueue.wait_text(j, "k", SLOTS) == "Platz 1 in der Warteschlange · noch unter 1 Min."
    assert jobqueue.wait_text(j, "run", SLOTS) is None


def test_wait_text_ohne_negative_zeit_und_abbruch():
    j = _jobs()
    j["run"]["progress"] = 150
    j["a"]["cancel"] = True
    assert jobqueue.wait_text(j, "b", SLOTS) == "Platz 1 in der Warteschlange · noch unter 1 Min."


def test_wait_text_zwei_plaetze():
    j = _jobs()
    assert jobqueue.wait_text(j, "b", {**SLOTS, "lokal": 2}) == "Platz 2 in der Warteschlange · noch ca. 2 Min."
