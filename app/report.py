"""Tagesbericht (seit v0.16.0): was gestern in RideAnalyzer/RigAnalyzer passiert ist – aus dem Ereignis-Tagebuch.
Reine Funktionen; versendet wird aus main.py um 07:00 (einmal je Tag, nach Neustart bis 12 Uhr nachgeholt)."""

import datetime as dt
import os
from collections import Counter, OrderedDict

import events

APP = {"ride": "Ride", "rig": "Rig"}
WEEKDAY = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
USER_KINDS = ("login", "analyse", "beispiel", "planung", "navi", "apk", "analyse_fehler")


def _n(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _hm(t):
    return dt.datetime.fromtimestamp(t).strftime("%H:%M")


def _short(name):
    n = (name or "").rsplit("/", 1)[-1]
    return n[:-4] if n.lower().endswith(".gpx") else n


def _list(items, limit=5):
    return ", ".join(items[:limit]) + (f" +{len(items) - limit}" if len(items) > limit else "")


def build(day, evs, health, space=None):
    """-> (Betreff, Text) oder None an einem Tag ganz ohne Nutzung, Admin-Aenderung oder Fehler.
    space: (heute, gestern) aus platz.json/platz-gestern.json (Host-Skript für den Platz-Bericht) oder None."""
    evs = sorted(evs, key=lambda e: e.get("t", 0))
    use = [e for e in evs if e.get("kind") in USER_KINDS and e.get("user") not in (None, "-")
           and not (e["kind"] == "login" and not e.get("ok"))]
    fails = [e for e in evs if e.get("kind") == "login" and not e.get("ok")]
    admin = [e for e in evs if e.get("kind") == "admin"]
    errs = [e for e in evs if e.get("kind") == "fehler"]
    now_space, prev_space = space or (None, None)
    low = space_low(now_space)
    if not (use or fails or admin or errs or low):
        return None
    cnt = Counter(e["kind"] for e in use)
    users = OrderedDict()
    for e in use:
        users.setdefault(e["user"], []).append(e)

    # je App ein Container (RA_APP) -> eigener Bericht mit eigenem Namen
    title = {"ride": "RideAnalyzer", "rig": "RigAnalyzer"}.get(os.environ.get("RA_APP", "").strip(), "RideAnalyzer/RigAnalyzer")
    subject = (f"{title} – Tagesbericht {day.strftime('%d.%m.%Y')}: {len(users)} aktiv, "
               f"{_n(cnt['analyse'], 'Analyse', 'Analysen')}, {_n(cnt['planung'], 'Planung', 'Planungen')}")
    if low:
        subject += f" · ⚠ Server nur {_gb(now_space['free'])} frei"
    out = [f"Tagesbericht für {WEEKDAY[day.weekday()]}, {day.strftime('%d.%m.%Y')}", ""]

    out.append("ÜBERBLICK")
    for app in ("ride", "rig"):
        mine = [e for e in use if e.get("app") == app]
        if not mine:
            continue
        c = Counter(e["kind"] for e in mine)
        bits = [f"{len({e['user'] for e in mine})} aktiv"]
        for k, one, many in (("analyse", "Analyse", "Analysen"), ("beispiel", "Beispiel", "Beispiele"),
                             ("planung", "Planung", "Planungen"), ("navi", "Navi-Fahrt", "Navi-Fahrten"),
                             ("apk", "App-Download", "App-Downloads")):
            if c[k]:
                bits.append(_n(c[k], one, many))
        out.append(f"  {APP[app]}: " + " · ".join(bits))
    if not use:
        out.append("  keine Nutzung")
    out.append("")

    if users:
        out.append("WER HAT WAS GEMACHT")
        for u, lst in users.items():
            apps = "+".join(APP[a] for a in ("ride", "rig") if any(e.get("app") == a for e in lst)) or "–"
            bits = [u, apps]
            def by(k, lst=lst):
                return [e for e in lst if e["kind"] == k]

            if by("login"):
                bits.append(_n(len(by("login")), "Anmeldung", "Anmeldungen"))
            if by("analyse"):
                bits.append(_n(len(by("analyse")), "Analyse", "Analysen") + " ("
                            + _list([f"{_short(e.get('name'))} {round(e.get('km') or 0)} km" for e in by("analyse")]) + ")")
            if by("beispiel"):
                bits.append(_n(len(by("beispiel")), "Beispiel", "Beispiele") + f" ({_list([e.get('name', '') for e in by('beispiel')])})")
            if by("planung"):
                kms = [f"{round(e.get('km') or 0)} km" for e in by("planung")]
                bits.append(_n(len(kms), "Planung", "Planungen") + f" ({_list(kms)})")
            if by("navi"):
                bits.append(_n(len(by("navi")), "Navi-Fahrt", "Navi-Fahrten") + f" ({_list([e.get('dest') or '?' for e in by('navi')])})")
            if by("apk"):
                bits.append("RigNavi geladen")
            if by("analyse_fehler"):
                bits.append(_n(len(by("analyse_fehler")), "abgelehnte Analyse", "abgelehnte Analysen"))
            out.append("  " + " · ".join(bits))
        out.append("")

    if fails or admin or errs:
        out.append("AUFFÄLLIGES")
        if fails:
            out.append(f"  {_n(len(fails), 'Fehlversuch', 'Fehlversuche')}: "
                       + _list([f"{e.get('tried') or e.get('user') or '?'} von {e.get('ip', '?')} ({_hm(e['t'])})" for e in fails], 10))
        for e in admin:
            out.append(f"  {e.get('user')} → {e.get('target')}: {e.get('what')} ({_hm(e['t'])})")
        for (lg, msg), k in Counter((e.get("logger", "?"), e.get("msg", "")) for e in errs).most_common(8):
            out.append(f"  {k}× {lg}: {msg}")
        out.append("")

    if health:
        out.append("BETRIEB")
        out.append(f"  Version {health.get('version', '?')} · Kartendaten {health.get('osm', '?')} · Valhalla "
                   f"{health.get('valhalla', '?')} · {round(health.get('disk_free_gb') or 0)} GB frei")
        out.append("")
    out += space_lines(now_space, prev_space)
    return subject, "\n".join(out).rstrip("\n") + "\n"


# ---------------------------------------------------------------- Platz auf dem Server
# Jeden Morgen sehen, was auf der Karte gross ist und was seit gestern dazugekommen ist.
GB = 1e9
LOW_FREE_GB = 10            # darunter kommt der Bericht auch an einem Tag ohne Nutzung, mit ⚠ im Betreff
TOP_DIRS, TOP_FILES = 8, 10
DIR_DELTA = 0.05 * GB       # kleinere Aenderungen je Ordner nicht erwaehnen
FILE_DELTA = 0.1 * GB


def _gb(b, sign=False):
    return (f"{b / GB:+.1f}" if sign else f"{b / GB:.1f}").replace(".", ",") + " GB"


def space_low(now):
    return bool(now) and now.get("free", GB * 1e3) < LOW_FREE_GB * GB


def space_lines(now, prev=None):
    """Abschnitt PLATZ AUF DEM SERVER: frei, groesste Ordner und Dateien, jeweils mit Aenderung seit gestern."""
    if not now or not now.get("total"):
        return []
    prev = prev or {}
    stamp = dt.datetime.fromtimestamp(now.get("t", 0)).strftime("%d.%m. %H:%M")
    head = f"{_gb(now['free'])} frei von {_gb(now['total'])} ({round(100 * now['free'] / now['total'])} %)"
    if prev.get("free") is not None:
        head += f", seit gestern {_gb(now['free'] - prev['free'], True)}"
    out = [f"PLATZ AUF DEM SERVER (Stand {stamp})", ("  ⚠ " if space_low(now) else "  ") + head]

    pdirs = prev.get("dirs") or {}
    dirs = sorted((now.get("dirs") or {}).items(), key=lambda kv: -kv[1])[:TOP_DIRS]
    if dirs:
        out.append("  Größte Ordner:")
        for path, b in dirs:
            note = ""
            if pdirs and path not in pdirs:
                note = "  (neu)"
            elif path in pdirs and abs(b - pdirs[path]) >= DIR_DELTA:
                note = f"  ({_gb(b - pdirs[path], True)})"
            out.append(f"    {_gb(b):>9}  {path}{note}")

    pfiles = prev.get("files") or {}
    nfiles = now.get("files") or {}
    files = sorted(nfiles.items(), key=lambda kv: -kv[1]["size"])
    if files:
        out.append("  Große Dateien (ab 500 MB):")
        for path, f in files[:TOP_FILES]:
            note = ""
            if prev and path not in pfiles:
                note = "  NEU"
            elif path in pfiles and abs(f["size"] - pfiles[path]["size"]) >= FILE_DELTA:
                note = f"  ({_gb(f['size'] - pfiles[path]['size'], True)})"
            out.append(f"    {_gb(f['size']):>9}  {path}{note}")
        if len(files) > TOP_FILES:
            new_rest = [p for p, _ in files[TOP_FILES:] if prev and p not in pfiles]
            out.append(f"    … {len(files) - TOP_FILES} weitere" + (f", davon {len(new_rest)} neu" if new_rest else ""))
    gone = sorted(p for p in pfiles if p not in nfiles)
    if gone:
        out.append("  Seit gestern weg: " + _list(gone, 5))

    docker = [r for r in now.get("docker") or [] if not str(r.get("reclaimable", "0B")).startswith("0B")]
    if docker:
        out.append("  Docker freigebbar: " + " · ".join(f"{r['type']} {r['reclaimable']} von {r['size']}" for r in docker)
                   + "  → ra platz")
    return out + [""]


# ---------------------------------------------------------------- Versand einmal je Tag
SEND_HOUR, LATEST_HOUR = 7, 12


def _marker(day):
    return os.path.join(events.DIR, f"report-sent-{day.isoformat()}")


def due(now):
    """Welcher Tag ist jetzt zu berichten? None vor 7 Uhr, nach 12 Uhr oder wenn schon verschickt."""
    if not SEND_HOUR <= now.hour < LATEST_HOUR:
        return None
    day = now.date() - dt.timedelta(days=1)
    return None if os.path.exists(_marker(day)) else day


def mark_sent(day):
    os.makedirs(events.DIR, exist_ok=True)
    with open(_marker(day), "w") as f:
        f.write(dt.datetime.now().isoformat())
