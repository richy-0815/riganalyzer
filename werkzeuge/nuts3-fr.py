#!/usr/bin/env python3
"""NUTS3-Grenzen Frankreichs fuer die MeteoAlarm-Zuordnung (Warnungen nennen dort NUTS3-Codes).
Quelle: Eurostat GISCO (ohne Konto), vereinfacht. Aufruf:
  python3 werkzeuge/nuts3-fr.py app/geo/nuts3_fr.json
"""
import json
import sys
import urllib.request

from shapely.geometry import mapping, shape

URL = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_20M_2013_4326_LEVL_3.geojson"


def main(out, tol=0.01):
    data = json.load(urllib.request.urlopen(URL, timeout=60))
    res = {}
    for f in data["features"]:
        p = f["properties"]
        if p.get("CNTR_CODE") != "FR":
            continue
        g = shape(f["geometry"]).simplify(tol, preserve_topology=True)
        m = mapping(g)
        res[p["NUTS_ID"]] = {"name": p.get("NAME_LATN", ""), "geometry": json.loads(json.dumps(m, default=list))}
    src = "Eurostat GISCO NUTS 2013 20M (vereinfacht) – MeteoAlarm nutzt die alten 2013er Codes (FR825 = Var)"
    txt = json.dumps({"source": src, "regions": res}, separators=(",", ":"))
    txt = txt.replace(".0,", ",")
    open(out, "w").write(txt)
    print(f"{len(res)} Regionen, {len(txt)} Bytes")


if __name__ == "__main__":
    main(sys.argv[1])
