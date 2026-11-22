#!/usr/bin/env bash
# Einmalig lokal: Offline-Kartenstil fuer RigNavi-Pakete erzeugen -> files/navistyle/
#   hell.json / dunkel.json  (Protomaps basemaps, Beschriftung deutsch)
#   fonts/<stack>/<bereich>.pbf, sprites/hell|dunkel(@2x).json|png  (protomaps/basemaps-assets)
# Platzhalter {KARTE} (Pfad zu karte.pmtiles) und {STIL} (Ordner stil/) ersetzt die App.
set -euo pipefail
BASEMAPS=${BASEMAPS:-5.7.2}
OUT=$(cd "$(dirname "$0")/.." && pwd)/navistyle
ASSETS=https://raw.githubusercontent.com/protomaps/basemaps-assets/main
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
rm -rf "$OUT"; mkdir -p "$OUT/fonts" "$OUT/sprites"

(cd "$TMP" && npm init -y >/dev/null && npm i -q "@protomaps/basemaps@$BASEMAPS" >/dev/null)
NODE_PATH="$TMP/node_modules" OUT="$OUT" node -e '
const b = require("@protomaps/basemaps"), fs = require("fs");
for (const [name, flavor] of [["hell", "light"], ["dunkel", "dark"]]) {
  const style = {
    version: 8, name: "RigNavi " + name,
    glyphs: "{STIL}/fonts/{fontstack}/{range}.pbf",
    sprite: "{STIL}/sprites/" + name,
    sources: { protomaps: { type: "vector", url: "pmtiles://{KARTE}",
      attribution: "© OpenStreetMap (ODbL) · Protomaps" } },
    layers: b.layers("protomaps", b.namedFlavor(flavor), { lang: "de" }),
  };
  fs.writeFileSync(process.env.OUT + "/" + name + ".json", JSON.stringify(style));
}'

# Schriften: Latein, Latein erweitert, Satzzeichen (FR/ES/CH/DE-Ortsnamen)
for f in "Noto Sans Regular" "Noto Sans Medium" "Noto Sans Italic"; do
  mkdir -p "$OUT/fonts/$f"
  for r in 0-255 256-511 512-767 7680-7935 8192-8447; do
    curl -sfL -o "$OUT/fonts/$f/$r.pbf" "$ASSETS/fonts/${f// /%20}/$r.pbf"
  done
done
for s in "" "@2x"; do
  for ext in json png; do
    curl -sfL -o "$OUT/sprites/hell$s.$ext" "$ASSETS/sprites/v4/light$s.$ext"
    curl -sfL -o "$OUT/sprites/dunkel$s.$ext" "$ASSETS/sprites/v4/dark$s.$ext"
  done
done
du -sh "$OUT"
