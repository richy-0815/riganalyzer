"""Wetter je Etappe (seit v0.13): Open-Meteo, Stunde zur Ankunft, Boeen fuers Gespann, Cache, Horizont."""
import datetime as dt

import pytest

from test_admin import basic, env  # noqa: F401  (Fixture env)

import weather

UTC = dt.timezone.utc
NOW = dt.datetime(2027, 5, 20, 8, 0, tzinfo=UTC)


def _om(n, start="2027-05-20T00:00", hours=24 * 16, gust_at=None):
    """Open-Meteo-Antwort fuer n Orte: Werte = Stunde, Boee an gust_at = 80."""
    t0 = dt.datetime.fromisoformat(start)
    times = [(t0 + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    one = {"hourly": {"time": times, "temperature_2m": [float(h % 24) for h in range(hours)],
                      "precipitation": [0.0] * hours, "wind_speed_10m": [10.0] * hours,
                      "wind_gusts_10m": [80.0 if gust_at == t else 20.0 for t in times],
                      "weather_code": [61] * hours}}
    return [one] * n if n > 1 else one


class Fake:
    def __init__(self, data):
        self.data, self.calls = data, []

    def __call__(self, url, params=None, **kw):
        self.calls.append(params)
        data = self.data(params) if callable(self.data) else self.data

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return data
        return R()


@pytest.fixture(autouse=True)
def clear_cache():
    weather._cache.clear()


def test_picks_hour_in_utc(monkeypatch):
    fake = Fake(lambda p: _om(len(p["latitude"].split(","))))
    monkeypatch.setattr(weather.httpx, "get", fake)
    eta = dt.datetime(2027, 5, 24, 14, 40, tzinfo=UTC)
    r = weather.forecast_points([(43.85, 6.51, eta)], now=NOW)[0]
    assert r["temp"] == 14.0 and r["code"] == 61 and r["text"] == "leichter Regen"
    assert fake.calls[0]["timezone"] == "UTC"


def test_gust_levels():
    assert weather.gust_level(49, True) is None
    assert weather.gust_level(50, True) == "tight"
    assert weather.gust_level(70, True) == "critical"
    assert weather.gust_level(75, False) == "info"
    assert weather.gust_level(60, False) is None
    assert weather.gust_level(59, False, moto=True) is None          # Motorrad (Ride): Hinweis ab 60
    assert weather.gust_level(60, False, moto=True) == "info"


def test_moto_flag_in_route_weather(monkeypatch):
    def gust65(p):
        d = _om(1, gust_at="2027-05-24T12:00")
        d["hourly"]["wind_gusts_10m"] = [65.0 if g == 80.0 else g for g in d["hourly"]["wind_gusts_10m"]]
        return d
    monkeypatch.setattr(weather.httpx, "get", Fake(gust65))
    monkeypatch.setattr(weather, "_now", lambda: NOW)
    monkeypatch.setattr(weather, "warnings_for", lambda stages: ({}, {}))
    body = {"rig": False, "moto": True, "stages": [{"n": 1, "pts": [[43.0, 5.0, "2027-05-24T10:00:00Z", 0]]}]}
    assert weather.route_weather(body)["stages"][0]["worst"]["gust_level"] is None     # 20 km/h
    body["stages"][0]["pts"][0][2] = "2027-05-24T12:00:00Z"
    assert weather.route_weather(body)["stages"][0]["worst"]["gust_level"] == "info"   # 65 km/h, ohne moto nichts
    body["moto"] = False
    weather._cache.clear()
    assert weather.route_weather(body)["stages"][0]["worst"]["gust_level"] is None


def test_beyond_horizon(monkeypatch):
    fake = Fake(lambda p: _om(len(p["latitude"].split(","))))
    monkeypatch.setattr(weather.httpx, "get", fake)
    eta = NOW + dt.timedelta(days=20)
    r = weather.forecast_points([(43.85, 6.51, eta)], now=NOW)[0]
    assert r == {"later": "ab 25.05. verfügbar"}        # 16 Tage vor der Ankunft
    assert fake.calls == []


def test_cache_hit(monkeypatch):
    fake = Fake(lambda p: _om(len(p["latitude"].split(","))))
    monkeypatch.setattr(weather.httpx, "get", fake)
    eta = dt.datetime(2027, 5, 24, 10, 0, tzinfo=UTC)
    pts = [(43.851, 6.511, eta), (43.852, 6.512, eta)]       # gleiche Rundung (0,05°)
    weather.forecast_points(pts, now=NOW)
    weather.forecast_points(pts, now=NOW)
    assert len(fake.calls) == 1 and fake.calls[0]["latitude"].count(",") == 0


def test_worst_along_stage(monkeypatch):
    monkeypatch.setattr(weather.httpx, "get", Fake(lambda p: _om(len(p["latitude"].split(",")), gust_at="2027-05-24T12:00")))
    monkeypatch.setattr(weather, "_now", lambda: NOW)
    body = {"rig": True, "stages": [{"n": 1, "pts": [[43.0, 5.0, "2027-05-24T10:00:00Z", 0],
                                                     [43.5, 5.5, "2027-05-24T12:10:00Z", 120],
                                                     [43.85, 6.51, "2027-05-24T15:30:00Z", 250]]}]}
    st = weather.route_weather(body)["stages"][0]
    assert st["end"]["temp"] == 15.0
    assert st["worst"]["gust"] == 80.0 and st["worst"]["gust_at"] == 120 and st["worst"]["gust_level"] == "critical"


def test_api_needs_login_and_limit(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    monkeypatch.setattr(weather.httpx, "get", Fake(lambda p: _om(len(p["latitude"].split(",")))))
    body = {"stages": [{"n": 1, "pts": [[43.0, 5.0, "2027-05-24T10:00:00Z"]]}]}
    assert env.post("/api/weather", json=body, headers={"user-agent": "okhttp/5"}).status_code == 401
    assert env.post("/api/weather", json=body, headers=basic("olaf")).status_code == 200
    many = {"stages": [{"n": 1, "pts": [[43.0, 5.0, "2027-05-24T10:00:00Z"]] * 41}]}
    assert env.post("/api/weather", json=many, headers=basic("olaf")).status_code == 422


def test_wmo_text():
    assert weather.wmo_text(61) == "leichter Regen"
    assert weather.wmo_text(0) == "klar"


def test_radar_latest_frame(env, monkeypatch):  # noqa: F811 – Fixture (Import oben)
    data = {"host": "https://tilecache.rainviewer.com", "radar": {"past": [{"time": 1, "path": "/v2/radar/aaa"},
                                                                           {"time": 2, "path": "/v2/radar/bbb"}]}}
    monkeypatch.setattr(weather.httpx, "get", Fake(data))
    weather._feed_cache.clear()
    r = env.get("/api/weather/radar", headers=basic("olaf"))
    assert r.status_code == 200
    assert r.json() == {"host": "https://tilecache.rainviewer.com", "path": "/v2/radar/bbb", "time": 2,
                        "tiles": "https://tilecache.rainviewer.com/v2/radar/bbb/256/{z}/{x}/{y}/2/1_1.png"}


def test_csp_allows_radar_tiles(env):  # noqa: F811 – Fixture (Import oben)
    csp = env.get("/login").headers["content-security-policy"]
    assert "https://tilecache.rainviewer.com" in csp and "connect-src 'self'" in csp
