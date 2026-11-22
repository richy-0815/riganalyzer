#!/usr/bin/env python3
"""Grenzen der MeteoAlarm-Warngebiete (0.40.0) – damit eine Warnung nur dann an einer Etappe steht, wenn ihr Gebiet
die Strecke wirklich beruehrt (vorher: in Laendern ohne Grenzen jede orange/rote Warnung des ganzen Landes,
z. B. La Palma auf der Fahrt nach Barcelona).

Quellen (ohne Konto):
  * EMMA_ID-Gebiete: geocodes.json von MeteoAlarm, wie es das Python-Paket „meteoalarm“ (MIT) mitliefert
  * NUTS2/NUTS3-Gebiete (HU, RO, HR, BE … nennen NUTS-Codes): Eurostat GISCO, 2013er und 2021er Stand
Frankreich bleibt in geo/nuts3_fr.json (werkzeuge/nuts3-fr.py). Aufruf:
  python3 werkzeuge/meteoalarm-areas.py app/geo/meteoalarm_areas.json
"""
import io
import json
import sys
import urllib.request
import zipfile

from shapely.geometry import mapping, shape

WHEEL = ("https://files.pythonhosted.org/packages/e5/69/267abfa8033212f7efa02fd0c3ea6f276fb9c3515ed66977cc0080eec4b2/"
         "meteoalarm-0.3.0-py3-none-any.whl")
GISCO = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_20M_{year}_4326_LEVL_{level}.geojson"
SKIP_NUTS = {"FR"}   # Frankreich: eigene Datei


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "rideanalyzer-tools"})  # noqa: S310 – feste https-Quelle
    return urllib.request.urlopen(req, timeout=120).read()  # noqa: S310 – feste https-Quelle


def _geom(g, tol):
    s = shape(g).simplify(tol, preserve_topology=True)
    if s.is_empty:
        return None
    m = json.loads(json.dumps(mapping(s), default=list))

    def rnd(c):
        return [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], 3), round(c[1], 3)]
    m["coordinates"] = rnd(m["coordinates"])
    return m


def main(out, tol=0.01):
    res = {}
    whl = zipfile.ZipFile(io.BytesIO(_get(WHEEL)))
    geo = json.loads(whl.read("meteoalarm/assets/geocodes.json"))
    for f in geo["features"]:
        p = f.get("properties") or {}
        if p.get("type") == "EMMA_ID" and p.get("code") and f.get("geometry"):
            g = _geom(f["geometry"], tol)
            if g:
                res[f"EMMA_ID:{p['code']}"] = g
    n_emma = len(res)
    for level in (2, 3):
        for year in (2021, 2013):   # 2013 gewinnt bei gleichem Code (MeteoAlarm nutzt teils noch alte Codes)
            for f in json.loads(_get(GISCO.format(year=year, level=level)))["features"]:
                p = f["properties"]
                if p.get("CNTR_CODE") in SKIP_NUTS:
                    continue
                g = _geom(f["geometry"], tol)
                if g:
                    res[f"NUTS{level}:{p['NUTS_ID']}"] = g
    src = ("MeteoAlarm geocodes.json (über Python-Paket meteoalarm 0.3.0) + Eurostat GISCO NUTS 2013/2021 20M, "
           f"vereinfacht ({tol}°)")
    txt = json.dumps({"source": src, "areas": res}, separators=(",", ":"))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(f"{n_emma} EMMA-Gebiete, {len(res) - n_emma} NUTS-Gebiete, {len(txt)} Bytes")


if __name__ == "__main__":
    main(sys.argv[1])
