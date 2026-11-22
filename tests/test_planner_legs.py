import json

import pytest

import planner

A = {"lat": 43.8475, "lon": 6.5130, "name": "Castellane"}
B = {"lat": 43.7956, "lon": 6.3986, "name": "Point Sublime", "mode": "noway"}
C = {"lat": 43.8463, "lon": 6.2213, "name": "Moustiers", "mode": "noway", "stop": True}
D = {"lat": 43.7486, "lon": 6.0911, "name": "Riez", "mode": "fast"}


def _enc(pts, f=1e6):
    out, pla, plo = [], 0, 0
    for la, lo in pts:
        for v, prev in ((round(la * f), pla), (round(lo * f), plo)):
            d = v - prev
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1f)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        pla, plo = round(la * f), round(lo * f)
    return "".join(out)


class Resp:
    status_code = 200
    headers = {"content-type": "application/json"}

    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def json(self):
        return self._d


@pytest.fixture
def valhalla(monkeypatch):
    calls = []

    def post(url, json=None, timeout=None):
        calls.append(json)
        locs = json["locations"]
        if json.get("_fail") or any(loc.get("lat") == 0 for loc in locs):
            return Resp({"error_code": 442, "error": "no path"}, 400)
        brk = [i for i, loc in enumerate(locs) if loc["type"] == "break"]
        legs = [{"summary": {"length": 10.0 * (b - a), "time": 600 * (b - a)},
                 "shape": _enc([(locs[k]["lat"], locs[k]["lon"]) for k in range(a, b + 1)])}
                for a, b in zip(brk, brk[1:])]
        return Resp({"trip": {"legs": legs, "summary": {"length": sum(leg["summary"]["length"] for leg in legs),
                                                          "time": sum(leg["summary"]["time"] for leg in legs)}}})
    monkeypatch.setattr(planner.httpx, "post", post)
    return calls


def test_leg_groups():
    assert planner.leg_groups([A, B, C, D], "fast") == [("noway", [0, 1, 2]), ("fast", [2, 3])]
    assert planner.leg_groups([A, {**B, "mode": None}, {**D, "mode": None}], "noway") == [("noway", [0, 1, 2])]


def test_single_mode_is_one_request(valhalla):
    r = planner.plan([A, {**B, "mode": "fast"}, D], "rig", "fast", {})
    assert len(valhalla) == 1 and r["leg_modes"] == ["fast", "fast"]


def test_mixed_modes_two_requests_in_order(valhalla):
    r = planner.plan([A, B, C, D], "rig", "fast", {"weight": 5, "trailer": True})
    assert len(valhalla) == 2
    assert valhalla[0]["costing_options"]["truck"]["use_highways"] == 0.0
    assert valhalla[1]["costing_options"]["truck"]["use_highways"] == 1.0
    assert [loc["lat"] for loc in valhalla[1]["locations"]] == [C["lat"], D["lat"]]
    assert valhalla[0]["locations"][-1]["type"] == "break" and valhalla[1]["locations"][0]["type"] == "break"
    assert r["leg_modes"] == ["noway", "noway", "fast"]
    assert len(r["stages"]) == 2 and r["stages"][0]["km"] == 20.0 and r["stages"][1]["km"] == 10.0
    assert r["km"] == 30.0 and r["gpx"].count("<trk>") == 2


def test_error_names_section(valhalla):
    bad = {**D, "lat": 0}
    with pytest.raises(planner.PlanError, match="Moustiers → Riez"):
        planner.plan([A, B, C, bad], "rig", "fast", {})


def test_error_says_vehicle_when_car_would_pass(monkeypatch):
    """0.26.2: Platz nur ueber gesperrte Zufahrt – mit Pkw ginge es: die Meldung sagt, dass es am Fahrzeug liegt."""
    def post(url, json=None, timeout=None):
        locs = json["locations"]
        if json["costing"] != "auto" and any(abs(loc["lat"] - C["lat"]) < 1e-9 for loc in locs):
            return Resp({"error_code": 442, "error": "no path"}, 400)
        return Resp({"trip": {"legs": [{"summary": {"length": 10.0, "time": 600},
                                         "shape": _enc([(loc["lat"], loc["lon"]) for loc in locs])}],
                              "summary": {"length": 10.0, "time": 600}}})
    monkeypatch.setattr(planner.httpx, "post", post)
    with pytest.raises(planner.PlanError) as e:
        planner.plan([A, B, C, D], "rig", "fast", {"length": 11.89, "weight": 5, "trailer": True})
    assert "mit eurem Fahrzeug" in str(e.value) and "Für Pkw geht es" in str(e.value)
    assert f"→ {C['name']}" in str(e.value)


def test_nav_route_error_explains_vehicle(monkeypatch):
    calls = []

    class R:
        def __init__(self, code, data):
            self.status_code, self._d = code, data
            self.content = json.dumps(data).encode()

        def json(self):
            return self._d

    def post(url, json=None, timeout=None):
        calls.append(json)
        if json["costing"] == "auto" and "costing_options" not in json:
            return R(200, {"trip": {}})
        return R(400, {"error_code": 442, "error": "No path could be found for input"})
    monkeypatch.setattr(planner.httpx, "post", post)
    body = {"locations": [{"lat": A["lat"], "lon": A["lon"]}, {"lat": D["lat"], "lon": D["lon"]}]}
    st, content, costing, _ = planner.nav_route(body, {"weight": 5, "trailer": True}, False)
    msg = json.loads(content)["ra_message"]
    assert st == 400 and costing == "truck" and "Für Pkw geht es" in msg
    assert calls[1]["costing"] == "auto" and "costing_options" not in calls[1]


def test_nav_route_error_without_car_route(monkeypatch):
    class R:
        status_code = 400
        content = b'{"error_code": 171}'

        def json(self):
            return {"error_code": 171}
    monkeypatch.setattr(planner.httpx, "post", lambda *a, **k: R())
    body = {"locations": [{"lat": A["lat"], "lon": A["lon"]}, {"lat": D["lat"], "lon": D["lon"]}]}
    msg = json.loads(planner.nav_route(body, {}, False)[1])["ra_message"]
    assert "Planungsgebiet" in msg


def test_heading_passed_to_valhalla(valhalla):
    """0.40.0: Zwischenpunkte aus einem GPX-Import tragen die Fahrtrichtung – Valhalla bekommt heading + Toleranz."""
    planner.plan([A, {**B, "mode": None, "heading": 271.6}, {**D, "mode": None, "heading": "kaputt"}], "rig")
    locs = valhalla[0]["locations"]
    assert locs[1]["heading"] == 272 and locs[1]["heading_tolerance"] == 60
    assert "heading" not in locs[0] and "heading" not in locs[2]
