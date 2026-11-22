"""RigAnalyzer-Endpunkte (seit 0.46.5 aus main.py, Trennung Ride/Rig Schritt 3c).

Bewertungen, Plaetze, Spritpreise, Tempo-Regeln, RigNavi (APK, POIs, Verkehr, Ping), Touren, Navi-Pakete und
Teilen. Alles haengt am Router `rig`, der auf dem Ride-Host 404 liefert; eingehaengt ueber hooks ("app.router",
angemeldet in rig_ext.py).
"""

import asyncio
import os
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

import activity
import auth
import events
import mailer
import osmdb
import package
import shares
import traffic
import trips
import usage
import web
from web import access, is_rig, who
from web import client_ip as _client_ip
from web import may_moderate as _may_moderate

HERE = os.path.dirname(os.path.abspath(__file__))
rig = APIRouter(dependencies=[Depends(web.rig_host)])


def _count_download(user, rng, fp):
    """Download im Verbrauchsbuch zaehlen; darf die Auslieferung nie stoeren."""
    try:
        usage.add(user, "rig", dl_bytes=_range_len(rng, os.path.getsize(fp)))
    except OSError:
        pass


@rig.post("/api/fuel/prices")
async def api_fuel_prices(request: Request):
    """Echte Dieselpreise fuer die Tankstellen einer Route – Login noetig.
    DE: Tankerkönig (seit v0.17.0); FR/ES/IT: offene Behoerdendaten (seit v0.23.0, fuelprice_eu)."""
    import fuelprice
    import fuelprice_eu
    try:
        body = await request.json()
        st = [(str(i), float(la), float(lo)) for i, la, lo in body["stations"]]
        fresh = bool(body.get("fresh"))   # „Jetzt prüfen“
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "Ungültige Anfrage.") from None
    if len(st) > 400:
        raise HTTPException(422, "Höchstens 400 Tankstellen.")
    res = await asyncio.to_thread(fuelprice.prices, st, fresh)
    for v in res.values():
        v.setdefault("src", "Tankerkönig")
        v.setdefault("cc", "DE")
    eu, pending = await asyncio.to_thread(fuelprice_eu.lookup, [s for s in st if s[0] not in res], fresh)
    res.update(eu)
    # Daenemark echt (Pflicht-APIs der Ketten), sonst DK/SE/NO amtlicher Landesschnitt
    # (avg: true – die Seite schaetzt damit statt mit dem deutschen Profilpreis)
    import fuelprice_dk
    import fuelprice_nordic
    dk, dk_pending = await asyncio.to_thread(fuelprice_dk.lookup, [s for s in st if s[0] not in res])
    res.update(dk)
    if dk_pending:
        pending = set(pending) | {"DK"}
    nordic, nordic_pending = await asyncio.to_thread(fuelprice_nordic.lookup, [s for s in st if s[0] not in res])
    res.update(nordic)
    pending = set(pending) | nordic_pending
    used = sorted({v["cc"] for v in res.values()})
    sources = {cc: (fuelprice.SOURCE if cc == "DE" else fuelprice_eu.SOURCE.get(cc)
                    or (fuelprice_dk.SOURCE if cc == "DK" and any(v.get("cc") == "DK" and not v.get("avg") for v in res.values())
                        else fuelprice_nordic.SOURCE.get(cc, cc)))
               for cc in used}
    return {"prices": res, "source": " · ".join(sources.values()) or fuelprice.SOURCE, "sources": sources,
            "enabled": True, "checked": int(time.time()), "pending": sorted(pending)}


@rig.post("/api/camps/fr")
async def api_camps_fr(request: Request):
    """Stell-/Campingplaetze aus DATAtourisme (seit v0.24.0): Angaben der Tourismusaemter zu den OSM-Plaetzen einer
    Route + Plaetze, die in OSM fehlen. Body: {"pois": [[osm, lat, lon, name]], "line": [[lat, lon, km]]}."""
    import frcamps
    try:
        body = await request.json()
        pois = [(str(o), float(la), float(lo), str(n or "")) for o, la, lo, n in body.get("pois", [])]
        line = [(float(la), float(lo), float(km)) for la, lo, km in body.get("line", [])]
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "Ungültige Anfrage.") from None
    if len(pois) > 3000 or len(line) > 5000:
        raise HTTPException(422, "Zu viele Punkte.")
    return await asyncio.to_thread(frcamps.lookup, pois, line)


@rig.post("/api/camps/laenge")
async def api_camps_laenge(request: Request):
    """Stellplatz-Laenge (seit 0.53.0, #30): Angaben aus Text/Website und Luftbild-Schaetzung fuer die Plaetze einer
    Route. Body: {"places": [[key, lat, lon], …]} in Wunsch-Reihenfolge. Fehlende werden im Hintergrund ausgewertet –
    der Browser fragt spaeter nochmal (state "pending")."""
    import platzlaenge
    if not request.state.user:
        raise HTTPException(401, "Bitte anmelden.")
    try:
        raw = [(k, la, lo) for k, la, lo in (await request.json())["places"]][:400]
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "Ungültige Anfrage.") from None
    # nur einreihen und gespeicherte Ergebnisse lesen – Merkmale/Website holt der Auswerte-Prozess selbst
    places = []
    for k, la, lo in raw:
        try:
            la, lo = float(la), float(lo)
        except (TypeError, ValueError):
            continue
        if platzlaenge.valid_key(str(k)) and -90 <= la <= 90 and -180 <= lo <= 180:
            places.append({"key": str(k), "lat": la, "lon": lo})
    res = await asyncio.to_thread(platzlaenge.lookup, places)
    return {"places": res, "status": await asyncio.to_thread(platzlaenge.status)}


@rig.get("/api/camps/laenge/bild")
async def api_camps_laenge_bild(request: Request, key: str):
    """Das ausgewertete Luftbild mit eingezeichnetem Umriss (zum Nachpruefen im Platz-Popup)."""
    import platzlaenge
    if not request.state.user:
        raise HTTPException(401, "Bitte anmelden.")
    path = await asyncio.to_thread(platzlaenge.image_path, key)
    if not path:
        raise HTTPException(404, "Kein Luftbild.")
    return FileResponse(path, media_type="image/jpeg")


def _review_user(request: Request, change=False):
    me = request.state.user
    if not me:
        raise HTTPException(401, "Bitte anmelden.")
    if change and request.state.via == "cookie" and not auth.csrf_ok(request):
        raise HTTPException(403, "Bitte die Seite neu laden und erneut versuchen.")
    return me


@rig.post("/api/reviews/lookup")
async def api_reviews_lookup(request: Request):
    """Kurzfassung (Anzahl, Schnitt, neueste) fuer die Plaetze einer Route. Body: {"places": [[key, lat, lon]]}."""
    import reviews
    _review_user(request)
    try:
        places = [(str(k), float(la), float(lo)) for k, la, lo in (await request.json())["places"]]
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "Ungültige Anfrage.") from None
    if len(places) > 3000:
        raise HTTPException(422, "Zu viele Plätze.")
    return {"places": await asyncio.to_thread(reviews.summaries, places)}


@rig.get("/api/reviews")
async def api_reviews_get(request: Request, key: str = "", lat: float | None = None, lon: float | None = None):
    import reviews
    me = _review_user(request)
    rows = await asyncio.to_thread(reviews.for_place, key, lat, lon)
    return {"reviews": rows, "mine": next((r for r in rows if r["user"] == me), None), "me": me,
            "admin": _may_moderate(request, me)}


@rig.post("/api/reviews")
async def api_reviews_save(request: Request):
    import reviews
    me = _review_user(request, change=True)
    try:
        key = await asyncio.to_thread(reviews.save, me, await request.json())
    except reviews.ReviewError as e:
        raise HTTPException(422, str(e)) from None
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    events.log("bewertung", me, "rig", platz=key)
    access.info("%s bewertet %s", me, key)
    return {"ok": True, "key": key}


@rig.delete("/api/reviews")
async def api_reviews_delete(request: Request, key: str, user: str = ""):
    import reviews
    me = _review_user(request, change=True)
    if user and user != me and not _may_moderate(request, me):
        raise HTTPException(403, "Nur eigene Bewertungen.")
    n = await asyncio.to_thread(reviews.delete, me, key, user if user and user != me else None)
    access.info("%s loescht Bewertung %s%s", me, key, f" von {user}" if user else "")
    return {"deleted": n}


@rig.get("/api/camps/photo")
async def api_camps_photo(request: Request, lat: float, lon: float):
    """Strassenfoto der Einfahrt (Mapillary, seit v0.25.0) – Login noetig, Schluessel bleibt auf dem Server."""
    import mapillary
    if not request.state.user:
        raise HTTPException(401, "Bitte anmelden.")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(422, "Ungültige Lage.")
    return await asyncio.to_thread(mapillary.photo, lat, lon)


@rig.get("/api/camps/photo/{image_id}")
async def api_camps_photo_image(image_id: str, request: Request):
    """Das Foto selbst (0.52.1): ueber den eigenen Server, damit der Browser nichts direkt bei Meta laedt."""
    import mapillary
    if not re.fullmatch(r"[0-9]{1,24}", image_id):
        raise HTTPException(404, "Nicht gefunden.")
    hit = await asyncio.to_thread(mapillary.image, image_id)
    if not hit:
        raise HTTPException(404, "Foto nicht verfügbar.")
    return Response(hit[0], media_type=hit[1])   # /api/ bekommt no-store (Middleware) – der Server-Speicher puffert


@rig.get("/api/foto/commons")
async def api_foto_commons(request: Request, datei: str = ""):
    """Vorschau eines Wikimedia-Commons-Fotos fuer die Detail-Karten (0.54.0, #31) – ueber den eigenen Server."""
    import fotos
    if not request.state.user:
        raise HTTPException(401, "Bitte anmelden.")
    try:
        hit = await asyncio.to_thread(fotos.commons, datei, request.state.user)
    except ValueError:
        raise HTTPException(422, "Ungültiger Dateiname.") from None
    except fotos.TooMany:
        raise HTTPException(429, "Zu viele Fotos auf einmal – bitte kurz warten.") from None
    if not hit:
        raise HTTPException(404, "Foto nicht verfügbar.")
    return Response(hit[0], media_type=hit[1])


@rig.get("/api/speedrules")
def api_speedrules():
    """Tempo-Tabelle je Land (seit v0.10) – RigNavi laedt sie online, offline gilt die Kopie im APK."""
    return FileResponse(os.path.join(HERE, "speedrules.json"), media_type="application/json",
                        headers={"Cache-Control": "no-cache"})


@rig.get("/api/sondermaut")
def api_sondermaut():
    """Sondermaut-Tabelle (Tunnel/Bruecken, seit 0.49.0): Tarife je Klasse fuer die Mautrechnung im Browser."""
    return FileResponse(os.path.join(HERE, "sondermaut.json"), media_type="application/json",
                        headers={"Cache-Control": "no-cache"})


@rig.get("/app/rignavi.apk")
@rig.get("/app/womo-navi.apk")   # alter Name, Links bleiben gueltig
def app_apk(request: Request):
    """RigNavi (Navi-App, Prototyp) zum Seitenladen aufs Handy – liegt in data/app, nicht im Image."""
    from fastapi.responses import FileResponse
    path = os.path.join(os.environ.get("DATA_DIR", "/data"), "app", "rignavi.apk")
    if not os.path.isfile(path):
        raise HTTPException(404, "App noch nicht bereitgestellt.")
    access.info("%s laedt RigNavi", who(request))
    events.log("apk", request.state.user, "rig")
    return FileResponse(path, media_type="application/vnd.android.package-archive", filename="rignavi.apk",
                        headers={"Cache-Control": "no-cache"})


@rig.post("/api/nav/pois")
async def api_nav_pois(request: Request):
    """Tankstellen + Stell-/Campingplaetze an einer Navi-Route (iPad-App, seit v0.27.0).
    Body: {"shape": [[lat, lon], …], "vehicle": {height, length, weight}}."""
    import navpois
    if not is_rig(request):
        raise HTTPException(404, "Nicht gefunden.")
    try:
        body = await request.json()
        res = await asyncio.to_thread(navpois.find, body.get("shape") or [], body.get("vehicle") or {}, osmdb.get())
    except navpois.NavPoiError as e:
        raise HTTPException(422, str(e)) from None
    except (ValueError, AttributeError):
        raise HTTPException(400, "Ungültige Anfrage.") from None
    access.info("%s Navi-POIs -> %d Tankstellen, %d Plätze", who(request), len(res["fuel"]), len(res["camps"]))
    return res


@rig.post("/api/nav/incidents")
async def api_nav_incidents(request: Request):
    """Staus/Sperrungen (Autobahn-API) auf einer Route: {"route": [[lat, lon], ...]}."""
    body = await request.json()
    route = [p for p in (body.get("route") or []) if isinstance(p, list) and len(p) == 2][:3000]
    try:
        route = [[float(a), float(b)] for a, b in route]
    except (TypeError, ValueError):
        raise HTTPException(400, "Ungültige Route.") from None
    res = await asyncio.to_thread(traffic.on_route, route)
    access.info("%s Verkehrslage fuer Route (%d Punkte) -> %d Meldungen", who(request), len(route), len(res["items"]))
    return res


def _trip_user(request: Request) -> str:
    """Reisen gibt es nur im RigAnalyzer; fremde Reisen sind fuer andere schlicht nicht vorhanden (404)."""
    if not is_rig(request):
        raise HTTPException(404, "Nicht gefunden.")
    return request.state.user


def _trip_out(user, t):
    return {"id": t["id"], "name": t["name"], "updated": t["updated"],
            "stages": sum(1 for s in t["stops"][1:-1] if s.get("stop")) + 1, "package": package.status(user, t["id"]),
            "stops": len(t["stops"]), "from": t["stops"][0]["name"], "to": t["stops"][-1]["name"],
            "note": (t.get("note") or "")[:140], "tour": "plan" in t,
            "analysis": trips.analysis_info(user, t["id"])}


# Dieselbe Ablage wie die Reisen fuers Navi, aber Speichern baut KEIN Offline-Paket (das macht erst „Ans Navi schicken“).
@rig.post("/api/tours")
async def api_tour_save(request: Request):
    """Tour speichern: Ziele + kompletter Planer-Stand (plan) + Notiz. Mit id: aktualisieren (alter Stand -> Version)."""
    user = _trip_user(request)
    try:
        t = await asyncio.to_thread(trips.save, user, await request.json())
    except trips.TripError as e:
        raise HTTPException(422, str(e)) from None
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    access.info("%s speichert Tour %s (%d Ziele)", who(request), t["id"], len(t["stops"]))
    return {"trip": t, "package": package.status(user, t["id"])}


@rig.put("/api/trips/{trip_id}/analysis")
async def api_trip_analysis_save(trip_id: str, request: Request):
    """Fertige Analyse zur Tour legen: {"gpx_hash", "result"} – beim Oeffnen der Tour sofort da, ohne neu zu rechnen."""
    user = _trip_user(request)
    try:
        body = await request.json()
        saved = await asyncio.to_thread(trips.save_analysis, user, trip_id, body.get("gpx_hash"), body.get("result"))
    except trips.TripError as e:
        raise HTTPException(422, str(e)) from None
    except (ValueError, AttributeError):
        raise HTTPException(400, "Ungültige Anfrage.") from None
    if not saved:
        raise HTTPException(404, "Tour nicht gefunden.")
    access.info("%s speichert Analyse zu Tour %s", who(request), trip_id)
    return {"saved": saved}


@rig.get("/api/trips/{trip_id}/analysis")
async def api_trip_analysis_get(trip_id: str, request: Request):
    """Gespeicherte Analyse zur Tour; eigene Tour ohne Analyse -> null (200, seit 0.47.0: sonst meldet der Browser beim
    Oeffnen jeder frischen Tour einen 404 in der Konsole). Fremde/unbekannte Tour bleibt 404."""
    user = _trip_user(request)
    if not trips.get(user, trip_id):
        raise HTTPException(404, "Tour nicht gefunden.")
    return await asyncio.to_thread(trips.load_analysis, user, trip_id)


@rig.get("/api/trips/{trip_id}/versions")
def api_trip_versions(trip_id: str, request: Request):
    return {"versions": trips.versions(_trip_user(request), trip_id)}


@rig.post("/api/trips/{trip_id}/restore")
async def api_trip_restore(trip_id: str, request: Request):
    user = _trip_user(request)
    try:
        v = (await request.json()).get("v")
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    t = trips.restore(user, trip_id, v)
    if not t:
        raise HTTPException(404, "Stand nicht gefunden.")
    access.info("%s holt Tour %s Stand %s zurueck", who(request), trip_id, v)
    return {"trip": t}


@rig.post("/api/trips/{trip_id}/copy")
def api_trip_copy(trip_id: str, request: Request):
    user = _trip_user(request)
    t = trips.copy(user, trip_id)
    if not t:
        raise HTTPException(404, "Reise nicht gefunden.")
    return {"trip": t}


@rig.post("/api/trips")
async def api_trip_save(request: Request):
    """Reise speichern (Webseite: "Ans Navi schicken") und den Paketbau anstossen."""
    user = _trip_user(request)
    try:
        t = trips.save(user, await request.json())
    except trips.TripError as e:
        raise HTTPException(422, str(e)) from None
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    try:
        package.request(user, t["id"])
    except package.TooMany as e:   # Reise ist gespeichert, nur der Paketbau wartet
        return {"trip": t, "package": package.status(user, t["id"]), "hint": str(e)}
    access.info("%s schickt Reise %s (%d Ziele) ans Navi", who(request), t["id"], len(t["stops"]))
    return {"trip": t, "package": package.status(user, t["id"])}


@rig.get("/api/trips")
def api_trip_list(request: Request):
    user = _trip_user(request)
    return [_trip_out(user, t) for t in trips.list_(user)]


@rig.get("/api/trips/{trip_id}")
def api_trip_get(trip_id: str, request: Request):
    user = _trip_user(request)
    t = trips.get(user, trip_id)
    if not t:
        raise HTTPException(404, "Reise nicht gefunden.")
    return {"trip": t, "package": package.status(user, trip_id)}


@rig.get("/api/trips/{trip_id}/way")
def api_trip_way(trip_id: str, request: Request):
    """Unterwegs-Orte + Fahrpausen einer Reise fuers iPad-Navi (0.48.0, Ticket #14) – aus der gespeicherten Analyse,
    dieselben Orte wie im Reisepaket. Ohne Analyse: leere Liste mit missing."""
    import reisedaten
    user = _trip_user(request)
    t = trips.get(user, trip_id)
    if not t:
        raise HTTPException(404, "Reise nicht gefunden.")
    out = {"pois": [], "pause": t.get("pause") or "auto"}
    a = trips.load_analysis(user, trip_id)
    res = (a or {}).get("result")
    if not isinstance(res, dict):
        return {**out, "missing": "analysis"}
    out["pois"] = reisedaten.way_pois(res["stages"] if res.get("trip") else [res])
    return out


@rig.delete("/api/trips/{trip_id}")
def api_trip_delete(trip_id: str, request: Request):
    user = _trip_user(request)
    if not trips.delete(user, trip_id):
        raise HTTPException(404, "Reise nicht gefunden.")
    package.delete(user, trip_id)
    access.info("%s loescht Reise %s", who(request), trip_id)
    return Response(status_code=204)


@rig.delete("/api/trips/{trip_id}/package")
def api_trip_package_delete(trip_id: str, request: Request):
    """0.33.0: nur das Paket vom Server nehmen (Platz auf dem Server) – die Reise bleibt, neu senden geht jederzeit."""
    user = _trip_user(request)
    if not trips.get(user, trip_id):
        raise HTTPException(404, "Reise nicht gefunden.")
    if package.status(user, trip_id)["state"] in ("queued", "building"):
        raise HTTPException(409, "Das Paket wird gerade gebaut – bitte danach löschen.")
    package.delete(user, trip_id)
    access.info("%s loescht Paket %s", who(request), trip_id)
    return Response(status_code=204)


@rig.post("/api/trips/{trip_id}/package")
def api_trip_package_rebuild(trip_id: str, request: Request):
    """0.51.0 (#21): Paket der gespeicherten Reise neu bauen („Neu bauen“ nach einem Fehler, Konto → Meine Touren)."""
    user = _trip_user(request)
    if not trips.get(user, trip_id):
        raise HTTPException(404, "Reise nicht gefunden.")
    if package.status(user, trip_id)["state"] in ("queued", "building"):
        raise HTTPException(409, "Das Paket wird schon gebaut.")
    try:
        package.request(user, trip_id)
    except package.TooMany as e:
        raise HTTPException(429, str(e)) from None
    access.info("%s baut Paket %s neu", who(request), trip_id)
    return {"package": package.status(user, trip_id)}


@rig.get("/api/trips/{trip_id}/package")
def api_package_manifest(trip_id: str, request: Request):
    m = package.manifest(_trip_user(request), trip_id)
    if not m:
        raise HTTPException(404, "Noch kein fertiges Paket.")
    return m


def _range_len(rng, size):
    """Bytes, die ein Range-Header 'bytes=a-b' liefert (kaputt/mehrfach/Suffix -> ganze Datei), hoechstens size."""
    try:
        m = re.fullmatch(r"\s*bytes=(\d+)-(\d*)\s*", rng or "")
        if not m:
            return size
        a = int(m.group(1))
        b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        return max(0, min(b - a + 1, size))
    except Exception:   # noqa: BLE001
        return size


@rig.get("/api/trips/{trip_id}/package/{path:path}")
def api_package_file(trip_id: str, path: str, request: Request):
    """Eine Paketdatei, mit HTTP-Range (Download setzt nach Abbruch fort). Nur im Manifest gelistete Dateien."""
    from fastapi.responses import FileResponse

    user = _trip_user(request)
    m, d = package.manifest(user, trip_id), package.current_dir(user, trip_id)
    if not m or path not in {f["name"] for f in m["files"]}:
        raise HTTPException(404, "Nicht im Paket.")
    if request.headers.get("range") is None:
        access.info("%s laedt Paketdatei %s/%s", who(request), trip_id, path)
    fp = os.path.join(d, path)
    _count_download(user, request.headers.get("range"), fp)
    # identity: sonst komprimiert die GZip-Middleware und Byte-Bereiche passen nicht mehr
    return FileResponse(fp, media_type="application/octet-stream",
                        headers={"Content-Encoding": "identity", "Cache-Control": "no-store"})


def _share_user(request: Request, change=False) -> str:
    """Nur RigAnalyzer, nur per Anmelde-Cookie (RigNavi-Zugang teilt nicht), aendernd nur mit CSRF."""
    user = _trip_user(request)
    if request.state.via != "cookie":
        raise HTTPException(404, "Nicht gefunden.")
    if change and not auth.csrf_ok(request):
        raise HTTPException(403, "Bitte die Seite neu laden und erneut versuchen.")
    return user


def _one_line(s, n=120):
    return " ".join("".join(c if c.isprintable() else " " for c in str(s)).split())[:n]


def _share_mail(to, subject, body):
    threading.Thread(target=mailer.send, args=(to, subject, body), kwargs={"app": "rig"}, daemon=True, name="share").start()


@rig.post("/api/shares")
async def api_share(request: Request):
    user = _share_user(request, change=True)
    try:
        b = await request.json()
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    if not isinstance(b, dict) or not isinstance(b.get("trip_id"), str) or not isinstance(b.get("to"), str):
        raise HTTPException(422, "Bitte einen Login-Namen eingeben.")
    try:
        e = shares.invite(user, b["to"], b["trip_id"])
    except shares.NotFound:
        raise HTTPException(404, "Nicht gefunden.") from None
    except shares.ShareError as err:
        raise HTTPException(422, str(err)) from None
    except shares.TooMany as err:
        raise HTTPException(429, str(err)) from None
    access.info("%s teilt Tour %s an %s", who(request), b["trip_id"], _one_line(b["to"], 64))
    if e and not e.get("renewed"):   # Wiederholung derselben Einladung: keine zweite Mail
        mail = (auth.users().get(e["to"]) or {}).get("mail")
        if mail:
            _share_mail(mail, _one_line(f"{user} hat dir die Tour „{e['name']}“ geteilt"),
                        f"{user} hat dir im RigAnalyzer die Tour „{_one_line(e['name'], 80)}“ geteilt.\n\n"
                        f"Annehmen oder ablehnen unter Mein Konto → Meine Touren → „Für dich geteilt“ "
                        f"(gilt {shares.TTL_DAYS} Tage).")
    return {"ok": True, "text": shares.SENT_TEXT}


@rig.get("/api/shares")
def api_shares(request: Request):
    user = _share_user(request)
    return JSONResponse({"inbox": shares.inbox(user), "outbox": shares.outbox(user)}, headers={"Cache-Control": "no-store"})


@rig.post("/api/shares/{sid}/accept")
def api_share_accept(sid: str, request: Request):
    user = _share_user(request, change=True)
    try:
        t = shares.accept(user, sid)
    except shares.NotFound:
        raise HTTPException(404, "Nicht gefunden.") from None
    except trips.TripError as err:
        raise HTTPException(422, str(err)) from None
    try:
        package.request(user, t["id"])
    except package.TooMany:
        pass   # Tour ist uebernommen; das Paket laesst sich spaeter in „Meine Touren“ anstossen
    access.info("%s nimmt geteilte Tour an (%s -> %s)", who(request), sid, t["id"])
    return {"trip": _trip_out(user, t)}


@rig.post("/api/shares/{sid}/decline")
def api_share_decline(sid: str, request: Request):
    user = _share_user(request, change=True)
    try:
        shares.decline(user, sid)
    except shares.NotFound:
        raise HTTPException(404, "Nicht gefunden.") from None
    access.info("%s lehnt geteilte Tour ab (%s)", who(request), sid)
    return {"ok": True}


@rig.delete("/api/shares/{sid}")
def api_share_withdraw(sid: str, request: Request):
    user = _share_user(request, change=True)
    try:
        shares.withdraw(user, sid)
    except shares.NotFound:
        raise HTTPException(404, "Nicht gefunden.") from None
    access.info("%s zieht Einladung zurueck (%s)", who(request), sid)
    return {"ok": True}


@rig.post("/api/nav/ping")
async def api_nav_ping(request: Request):
    """RigNavi meldet sich waehrend der Navigation (alle 2 min, wenn Netz da ist): Ziel, Rest, Ankunft, Quelle,
    Position nur mit Zustimmung des Fahrers (die App schickt sie sonst nicht). Seit 0.36.1 mit Zustimmung auch
    ohne Navigation, solange die App offen ist: {"idle": true, lat, lon} alle 5 min."""
    if not is_rig(request):
        raise HTTPException(404, "Nicht gefunden.")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage.") from None
    activity.heartbeat(request.state.user, _client_ip(request), body if isinstance(body, dict) else {})
    dest = str((body if isinstance(body, dict) else {}).get("dest") or "")[:80]
    key = (request.state.user, dest, time.strftime("%Y-%m-%d"))
    if dest and key not in _navi_logged:          # eine Navi-Fahrt je Ziel und Tag, ohne Positionen
        _navi_logged.add(key)
        events.log("navi", request.state.user, "rig", dest=dest)
    return Response(status_code=204)


_navi_logged = set()
