"""Aktivitaet der Logins fuer den Admin-Bereich (seit v0.9.4).

- Anmeldungen (Zeit, IP, Geraet, ok/Fehlversuch): je Login die letzten 20, hoechstens 30 Tage
- zuletzt gesehen: Zeit, IP, Geraet, Taetigkeit (aus dem Aufruf abgeleitet)
- RigNavi-Herzschlag waehrend der Navigation: Ziel, Rest, Ankunft, Quelle, Position nur mit Zustimmung
  (auf ~1 km gerundet, nur der letzte Punkt, nach 24 h weg); seit 0.36.1 mit Zustimmung auch ohne Navigation
  („idle“, App offen, alle 5 min)
Im Speicher, jede Minute nach data/activity.json gesichert.
"""

import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout

MAX_LOGINS, LOGIN_DAYS = 20, 30
ONLINE_S, NAVI_S, POS_S = 300, 600, 24 * 3600
_lock = threading.Lock()
_state = {"logins": {}, "seen": {}, "navi": {}}
_rdns_cache = {}
_rdns_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rdns")

ACTIONS = [
    ("POST", "/api/plan", "plant eine Route"),
    ("POST", "/api/analyze", "prüft eine GPX"),
    ("POST", "/api/demo/", "schaut ein Beispiel an"),
    ("POST", "/api/trips", "schickt eine Reise ans Navi"),
    ("GET", "/api/trips/", "lädt ein Reisepaket"),
    ("POST", "/api/nav/route", "Navi berechnet Route"),
    ("POST", "/api/nav/pois", "Navi holt Tankstellen und Plätze"),
    ("GET", "/api/geocode", "sucht einen Ort"),
    ("GET", "/admin", "im Admin-Bereich"),
]


def _file():
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "activity.json")


def action_for(method, path):
    for m, prefix, text in ACTIONS:
        if method == m and (path == prefix or (prefix.endswith("/") and path.startswith(prefix))):
            if prefix == "/api/trips/" and "/package/" not in path:
                return None
            return text
    return None


def rdns(ip):
    """Hostname der IP (zeigt grob den Anbieter) – ohne fremden Dienst, mit kurzem Zeitlimit, gecacht."""
    if ip in _rdns_cache:
        return _rdns_cache[ip]
    try:
        host = _rdns_pool.submit(lambda: socket.gethostbyaddr(ip)[0]).result(timeout=1.0)
    except (OSError, FutTimeout):
        host = ""
    _rdns_cache[ip] = host
    return host


def record_login(user, ip, device, ok):
    now = time.time()
    with _lock:
        lst = [e for e in _state["logins"].get(user, []) if now - e["t"] < LOGIN_DAYS * 86400]
        lst.insert(0, {"t": now, "ip": ip, "device": device, "ok": bool(ok)})
        _state["logins"][user] = lst[:MAX_LOGINS]


def logins(user):
    now = time.time()
    with _lock:
        return [dict(e) for e in _state["logins"].get(user, []) if now - e["t"] < LOGIN_DAYS * 86400]


def of(user):
    """Zuletzt gesehen + laufende Navigation dieses Logins (fuer „Meine Daten“)."""
    with _lock:
        return {"zuletzt_gesehen": dict(_state["seen"].get(user, {})), "navigation": dict(_state["navi"].get(user, {}))}


def touch(user, ip, device, method, path):
    now = time.time()
    act = action_for(method, path)
    with _lock:
        s = _state["seen"].setdefault(user, {})
        s.update(t=now, ip=ip, device=device)
        if act:
            s.update(action=act, action_t=now)


def heartbeat(user, ip, d):
    now = time.time()
    with _lock:
        idle_pos = isinstance(d.get("lat"), (int, float)) and isinstance(d.get("lon"), (int, float))
        if d.get("ended"):
            _state["navi"].pop(user, None)
        elif d.get("idle") and not idle_pos:
            pass   # „App offen“ ohne Position (sollte die App nicht schicken) – laufende Fahrt nicht ueberschreiben
        elif d.get("idle"):   # App offen ohne Navigation, Position nur mit Zustimmung (alle 5 min)
            _state["navi"][user] = {"t": now, "dest": "", "source": None, "idle": True,
                                    "lat": round(float(d["lat"]), 2), "lon": round(float(d["lon"]), 2)}
        else:
            n = {"t": now, "dest": str(d.get("dest") or "")[:80], "source": d.get("source") if d.get("source") in ("package", "server") else None}
            for k in ("remaining_m", "eta_s"):
                if isinstance(d.get(k), (int, float)):
                    n[k] = float(d[k])
            if isinstance(d.get("lat"), (int, float)) and isinstance(d.get("lon"), (int, float)):
                n["lat"], n["lon"] = round(float(d["lat"]), 2), round(float(d["lon"]), 2)   # ~1 km
            _state["navi"][user] = n
        s = _state["seen"].setdefault(user, {})
        s.update(t=now, ip=ip, device="RigNavi")
    return None


def snapshot(names):
    now = time.time()
    out = []
    with _lock:
        for n in names:
            s = dict(_state["seen"].get(n, {}))
            nav = _state["navi"].get(n)
            if nav and now - nav["t"] > POS_S:
                nav = None
            elif nav:
                nav = dict(nav)
                if now - nav["t"] > NAVI_S:
                    nav["stale"] = True       # meldet sich nicht mehr (offline oder beendet ohne Abmeldung)
            if not s:
                state = "nie"
            elif now - s["t"] < ONLINE_S:
                state = "online"
            elif time.strftime("%Y-%m-%d", time.localtime(s["t"])) == time.strftime("%Y-%m-%d", time.localtime(now)):
                state = "heute"
            else:
                state = "frueher"
            action = s.get("action") if s.get("action_t") and now - s["action_t"] < ONLINE_S else None
            out.append({"name": n, "state": state, "seen": s.get("t"), "ip": s.get("ip"), "device": s.get("device"),
                        "action": action, "navi": nav})
    for u in out:
        u["host"] = rdns(u["ip"]) if u["ip"] else ""
    return out


def save():
    with _lock:
        data = json.dumps(_state)
    tmp = _file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, _file())


def load():
    try:
        with open(_file(), encoding="utf-8") as f:
            d = json.load(f)
        with _lock:
            for k in _state:
                _state[k] = d.get(k, {})
    except (OSError, ValueError):
        pass


def reset_for_tests(load=False):
    with _lock:
        for k in _state:
            _state[k] = {}
    _rdns_cache.clear()
    if load:
        globals()["load"]()


def _saver():
    while True:
        time.sleep(60)
        try:
            save()
        except OSError:
            pass


def start():
    load()
    threading.Thread(target=_saver, name="activity", daemon=True).start()
