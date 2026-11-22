"""Kartenstreifen fuer das RigNavi-Offline-Paket: Ausschnitt aus dem taeglichen Protomaps-Build
(frei, ohne Konto) per HTTP-Range mit go-pmtiles – es werden nur die noetigen Teile geladen.
Bis Zoom 14 (wie OpenFreeMap): Zoom 15 vervierfacht die Groesse, MapLibre vergroessert selbst.
Dazu der Kartenstil hell/dunkel samt Schriften und Symbolen (einmalig erzeugt: tools/navistyle.sh).
"""

import shutil
import subprocess

import httpx

BUILDS = "https://build-metadata.protomaps.dev/builds.json"
BUILD_URL = "https://build.protomaps.com/{key}"
STYLE_SRC = "/navistyle"


def latest_build(fetch=httpx.get):
    r = fetch(BUILDS, timeout=30)
    r.raise_for_status()
    return BUILD_URL.format(key=r.json()[-1]["key"])


def extract(region_geojson, out, build_url, maxzoom=14, run=subprocess.run):
    run(["pmtiles", "extract", build_url, out, f"--region={region_geojson}", f"--maxzoom={maxzoom}",
         "--download-threads=4"], check=True, capture_output=True, timeout=3600)


def copy_style(dst_dir, src=STYLE_SRC):
    shutil.copytree(src, f"{dst_dir}/stil", dirs_exist_ok=True)
