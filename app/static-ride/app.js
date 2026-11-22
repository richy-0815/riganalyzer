/* RideAnalyzer – Prototyp-Oberflaeche */
"use strict";

const PROFILES = {
  p1: { label: "P1 Solo",          text: "Solotour: zügig, keine Pausen, flüssiges Fahren.",
        w: { fluss: .4, ort: .25, belag: .2, kurven: .15 } },
  p2: { label: "P2 kleine Gruppe", text: "Kleine Gruppe: zügig, Pausenimbiss, flüssiges Fahren.",
        w: { fluss: .35, ort: .2, belag: .15, pause: .15, kurven: .1, landschaft: .05 } },
  p3: { label: "P3 Gruppe",        text: "Gruppe: gemütlich, Sehenswürdigkeiten, Essenspause – und alle bleiben zusammen.",
        w: { sight: .25, landschaft: .2, pause: .2, fluss: .15, belag: .1, ort: .1 } },
  p4: { label: "P4 Kurven (Idee)", text: "Vorschlag: Kurven und kleine Straßen, wenig Ort.",
        w: { kurven: .4, strasse: .2, ort: .15, belag: .15, fluss: .1 } },
};
const AXES = ["fluss", "kurven", "ort", "belag", "landschaft", "strasse", "sight", "pause"];
const SEG_AXES = ["fluss", "kurven", "ort", "belag", "landschaft", "strasse"];
const BAND_ROWS = [["fluss", "Fahrfluss"], ["kurven", "Kurven"], ["ort", "Außerorts"], ["belag", "Belag"], ["landschaft", "Landschaft"]];
const LAND_LABEL = { wald: "Wald", wasser: "Wasser", feld: "Feld", wiese: "Wiese", siedlung: "Siedlung", gewerbe: "Gewerbe" };
const LAYER_LABEL = { profil: ["passt weniger", "passt gut"], fluss: ["viele Unterbrechungen", "fließt"],
  kurven: ["geradeaus", "kurvig"], ort: ["innerorts", "außerorts"], belag: ["holprig", "glatt"],
  landschaft: ["bebaut", "Natur"], strasse: ["große Straße", "kleine Straße"] };

const $ = (id) => document.getElementById(id);

/* Laufende Analyse: ein Lauf zur Zeit, abbrechbar – der Planer-Knopf und „Abbrechen“ im Fortschritt nutzen das. */
const Analysis = {
  running: false, cancelled: false, lastCancelled: false, job: null,
  Stopped: class extends Error {},
  begin() { this.running = true; this.cancelled = false; this.job = null; this.emit(); },
  end() { this.lastCancelled = this.cancelled; this.running = false; this.job = null; this.emit(); },
  setJob(id) { this.job = id; if (this.cancelled) this.send(); },
  cancel() {
    if (!this.running || this.cancelled) return;
    this.cancelled = true;
    $("progress-msg").textContent = "wird abgebrochen …";
    this.send(); this.emit();
  },
  send() {
    if (!this.job) return;   // noch beim Hochladen: setJob schickt es hinterher
    const csrf = (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
    fetch(`/api/jobs/${this.job}/cancel`, { method: "POST", headers: { "X-RA-CSRF": csrf } }).catch(() => {});
  },
  emit() {
    const b = $("progress-cancel");
    if (b) { b.hidden = !this.running; b.disabled = this.cancelled; }
    document.dispatchEvent(new CustomEvent("analysis-state"));
  },
};
window.Analysis = Analysis;
document.addEventListener("click", (e) => { if (e.target && e.target.id === "progress-cancel") Analysis.cancel(); });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (x, d = 0) => Number(x).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

const state = { routes: [], active: -1, profile: "p3", layer: "profil", tab: "notes" };
try { state.profile = localStorage.getItem("ra.profile") || state.profile; } catch { /* privat */ }
if (!PROFILES[state.profile]) state.profile = "p3";

/* ---------------------------------------------------------------- Farben */
function scaleColor(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return cssVar("--na");
  const stops = [cssVar("--s0"), cssVar("--s1"), cssVar("--s2"), cssVar("--s3")];
  const t = Math.max(0, Math.min(1, v / 100)) * (stops.length - 1);
  const i = Math.min(Math.floor(t), stops.length - 2);
  return mix(stops[i], stops[i + 1], t - i);
}
function mix(a, b, t) {
  const pa = hex(a), pb = hex(b);
  const c = pa.map((x, i) => Math.round(x + (pb[i] - x) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
function hex(h) {
  h = h.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

/* ---------------------------------------------------------------- Bewertung */
function weighted(scores, w) {
  let s = 0, n = 0;
  for (const [k, wk] of Object.entries(w)) {
    const v = scores[k];
    if (v === null || v === undefined) continue;
    s += v * wk; n += wk;
  }
  return n ? s / n : null;
}
const routeScore = (r, p) => weighted(Object.fromEntries(AXES.map((a) => [a, r.axes[a].score])), PROFILES[p].w);
function segScore(seg, layer) {
  if (layer === "profil") {
    const w = {};
    for (const [k, v] of Object.entries(PROFILES[state.profile].w)) if (SEG_AXES.includes(k)) w[k] = v;
    return weighted(seg.sc, w);
  }
  return seg.sc[layer];
}

/* ---------------------------------------------------------------- Karte */
// Startansicht und Grenzen: Europa mit Luft drumherum, nicht viel weiter heraus/weg
const EUROPE = L.latLngBounds([35.5, -10.5], [70.5, 31]);
const map = L.map("map", { zoomControl: true, preferCanvas: true, zoomSnap: 0.25,
  // Mausrad: Leaflet-Standard 60 px je Stufe war mit zoomSnap 0.25 zaeh – jetzt etwa wie die +/−-Knoepfe
  wheelPxPerZoomLevel: 22, wheelDebounceTime: 20, maxBounds: EUROPE.pad(0.4), maxBoundsViscosity: 0.9 });
// Startbild: Mitteleuropa von Edinburgh bis Madrid, Brest bis Istanbul;
const START = L.latLngBounds([40.0, -9.5], [56.8, 29.5]);
const START_PORTRAIT = L.latLngBounds([44.0, 3.0], [55.0, 19.0]);
function fitEurope() {
  // erst wenn die Karte ihre echte Groesse hat (Layout fertig), sonst stimmt der Zoom nicht
  map.invalidateSize();
  map.setMinZoom(0);
  // Hochformat (Handy): Mitteleuropa statt Spitzbergen bis Afrika
  const sz = map.getSize();
  map.fitBounds(sz.x < sz.y ? START_PORTRAIT : START, { animate: false });
  map.setMinZoom(Math.max(2.5, Math.min(map.getZoom(), map.getBoundsZoom(EUROPE))));
}
fitEurope();
window.addEventListener("load", () => setTimeout(() => { if (state.active < 0) fitEurope(); }, 50));
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18, referrerPolicy: "strict-origin-when-cross-origin",
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const markerLayers = { notes: L.layerGroup(), sights: L.layerGroup(), food: L.layerGroup(), fuel: L.layerGroup(), events: L.layerGroup(), curves: L.layerGroup(), radar: L.layerGroup() };
/* Regenradar (wie im RigAnalyzer): letztes RainViewer-Bild ueber den Server, alle 10 min neu, solange die Ebene an ist */
let radarTimer = 0;
async function loadRadar() {
  try {
    const res = await fetch("/api/weather/radar");
    if (!res.ok) return;
    const r = await res.json();
    markerLayers.radar.clearLayers();
    L.tileLayer(r.tiles, { opacity: 0.6, maxNativeZoom: 7, maxZoom: 18, zIndex: 400,
      attribution: `Radar: <a href="https://www.rainviewer.com" target="_blank" rel="noopener">RainViewer</a> (${new Date(r.time * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })})` }).addTo(markerLayers.radar);
  } catch { /* Zusatz */ }
}
markerLayers.radar.on("add", () => { loadRadar(); clearInterval(radarTimer); radarTimer = setInterval(loadRadar, 600000); });
markerLayers.radar.on("remove", () => clearInterval(radarTimer));
// Richtgeschwindigkeit aus dem Radius (gemuetlich bis zuegig), z. B. "ca. 25–35 km/h · "
const vRange = (c) => (c.v ? (c.v[0] === c.v[1] ? `ca. ${c.v[0]} km/h · ` : `ca. ${c.v[0]}–${c.v[1]} km/h · `) : "");
const CURVE_LABEL = { kehre: "Spitzkehre", eng: "Enge Kurve", mittel: "Mittlere Kurve", weit: "Weite Kurve" };
const hm = (min) => `${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, "0")} h`;
const pin = (cls, size = 14) => L.divIcon({ className: "", html: `<div class="pin ${cls}"></div>`, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
let cursor = null;

function drawRoute() {
  routeLayer.clearLayers();
  Object.values(markerLayers).forEach((l) => l.clearLayers());
  const r = state.routes[state.active];
  if (!r) return;
  const casing = cssVar("--card") || "#fff";
  // Vergleich: die anderen Touren duenn in ihrer Farbe darunter, antippen waehlt sie
  state.routes.forEach((o, i) => {
    if (i === state.active) return;
    L.polyline(o.segments.map((seg) => seg.c), { color: CompareUI.colorOf(i), weight: 4, opacity: .8 })
      .bindTooltip(`${esc(o.name)} – antippen zum Anzeigen`, { sticky: true })
      .on("click", () => selectRoute(i, false)).addTo(routeLayer);
  });
  L.polyline(r.segments.map((seg) => seg.c), { color: casing, weight: 10, opacity: .75, interactive: false }).addTo(routeLayer);
  r.segments.forEach((seg) => {
    const v = segScore(seg, state.layer);
    const line = L.polyline(seg.c, { color: scaleColor(v), weight: 6, opacity: .95, dashArray: v === null || v === undefined ? "4 6" : null });
    line.bindTooltip(segTip(seg, v), { sticky: true });
    line.on("mouseover", () => moveCursor(seg.c[Math.floor(seg.c.length / 2)]));
    line.addTo(routeLayer);
  });
  const first = r.segments[0].c[0], lastSeg = r.segments[r.segments.length - 1], last = lastSeg.c[lastSeg.c.length - 1];
  L.marker(first, { icon: pin("start") }).bindTooltip("Start").addTo(routeLayer);
  L.marker(last, { icon: pin("end") }).bindTooltip("Ziel").addTo(routeLayer);

  for (const p of r.pois) {
    const grp = { sight: "sights", food: "food", pause: "food", fuel: "fuel" }[p.group];
    if (!grp || (grp === "sights" && p.score < 1.5)) continue;
    L.marker([p.lat, p.lon], { icon: pin(grp === "sights" ? "sight" : grp) })
      .bindPopup(`<b>${esc(p.name || p.label)}</b><br>${esc(p.label)}${p.ele ? ` (${esc(p.ele)} m)` : ""} · bei km ${fmt(p.km, 1)} · nach ${hm(p.tmin)} Fahrzeit · ${p.off} m abseits${p.wiki ? " · Wikipedia" : ""}`)
      .addTo(markerLayers[grp]);
  }
  for (const e of r.events) {
    L.marker([e.lat, e.lon], { icon: pin("event", 10) }).bindTooltip(`${esc(e.label)} bei km ${fmt(e.km, 1)}`).addTo(markerLayers.events);
  }
  for (const c of r.curves) {
    if (c.urban) continue;
    L.marker([c.lat, c.lon], { icon: pin("curve", 9) })
      .bindTooltip(`${CURVE_LABEL[c.cls]} bei km ${fmt(c.km, 1)} · ${vRange(c)}Radius ≈ ${c.r} m · ${c.angle}°${c.s ? " · Wechselkurve" : ""}${c.rough ? " · holprig/schmal" : ""}`)
      .addTo(markerLayers.curves);
  }
  for (const n of r.notes || []) {
    L.marker([n.lat, n.lon], { icon: pin("note", 16) }).bindPopup(`<b>${esc(n.label)}</b><br>${esc(n.text)}`).addTo(markerLayers.notes);
  }
  syncToggles();
  drawLegend();
}

function segTip(seg, v) {
  const parts = [`<b>km ${fmt(seg.km0, 1)}–${fmt(seg.km1, 1)}</b>`, esc(seg.roadl) + (seg.urban ? " · innerorts" : "")];
  if (seg.ms) parts.push(`Tempolimit laut Karte: ${seg.ms} km/h`);
  if (seg.surf) parts.push(`Belag: ${esc(seg.surf)}`);
  if (seg.land) parts.push(`Umgebung: ${LAND_LABEL[seg.land] || seg.land}${seg.river ? " · am Wasser" : ""}`);
  parts.push(`Wert: ${v === null || v === undefined ? "keine Daten" : Math.round(v)}`);
  return parts.join("<br>");
}

function moveCursor(ll) {
  if (!ll) return;
  if (!cursor) cursor = L.marker(ll, { icon: pin("cursor", 14), interactive: false, zIndexOffset: 1000 }).addTo(map);
  else cursor.setLatLng(ll);
}

/* Alter der Kartendaten als Farbe: frisch blau -> ~2 Monate gruen -> ab 4 Monaten orange */
const AGE_STOPS = [[0, [34, 139, 230]], [60, [64, 192, 87]], [120, [247, 103, 7]]];
const ageDays = (iso) => (iso ? Math.max(0, (Date.now() - new Date(iso + "T12:00:00")) / 864e5) : 999);
function ageColor(d) {
  let i = 0;
  while (i < AGE_STOPS.length - 2 && d > AGE_STOPS[i + 1][0]) i++;
  const [d0, c0] = AGE_STOPS[i], [d1, c1] = AGE_STOPS[i + 1];
  const t = Math.min(1, Math.max(0, (d - d0) / (d1 - d0)));
  return `rgb(${c0.map((v, k) => Math.round(v + (c1[k] - v) * t)).join(",")})`;
}
const ageText = (d) => (d < 1.5 ? "frisch" : d < 45 ? `${Math.round(d)} Tage alt` : `${Math.round(d / 30.4)} Monate alt`);
const deDate = (iso) => (iso || "?").split("-").reverse().join(".");
const AGE_LEGEND = `<span class="cov-leg">Kartenstand: <i style="background:linear-gradient(90deg,${ageColor(0)},${ageColor(60)},${ageColor(120)})"></i> frisch → 2 Mon. → 4 Mon.+ · <i class="dash"></i> in Arbeit</span>`;

/* Abdeckung: welche Gebiete liegen lokal vor (Farbe = Alter) bzw. sind in Arbeit (grau gestrichelt) */
const covLayer = L.layerGroup();
async function loadCoverage(fit) {
  try {
    const data = await (await fetch("/api/coverage")).json();
    covLayer.clearLayers();
    const gj = L.geoJSON(data, {
      style: (f) => {
        if (f.properties.status !== "lokal")
          return { color: "#adb5bd", weight: 2, dashArray: "6 5", fillColor: "#adb5bd", fillOpacity: 0.1 };
        const c = ageColor(ageDays(f.properties.built));
        return { color: c, weight: 1, fillColor: c, fillOpacity: 0.3 };
      },
      onEachFeature: (f, l) => {
        const p = f.properties;
        l.bindTooltip(p.status === "lokal" ? `<b>${esc(p.name)}</b><br>lokale Kartendaten · Stand ${esc(deDate(p.built))} · ${esc(ageText(ageDays(p.built)))}`
          : `<b>${esc(p.name)}</b><br>${p.status} · fertig ca. ${new Date(p.eta * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`, { sticky: true });
      },
    });
    gj.addTo(covLayer);
    if (fit) fitEurope();
  } catch { /* Abdeckung ist Zusatz */ }
}
$("cov-toggle").addEventListener("change", () => {
  if ($("cov-toggle").checked) { covLayer.addTo(map); if (state.active < 0) $("legend").innerHTML = AGE_LEGEND; }
  else { map.removeLayer(covLayer); if (state.active < 0) $("legend").innerHTML = ""; }
});

function syncToggles() {
  document.querySelectorAll("#toggles input[data-m]").forEach((cb) => {
    const l = markerLayers[cb.dataset.m];
    if (cb.checked) l.addTo(map); else map.removeLayer(l);
  });
}

function drawLegend() {
  const [lo, hi] = LAYER_LABEL[state.layer];
  const sw = [0, 33, 66, 100].map((v) => `<i style="background:${scaleColor(v)}"></i>`).join("");
  $("legend").innerHTML = `<span>${esc(lo)}</span>${sw}<span>${esc(hi)}</span><span>· gestrichelt: keine Daten</span>`;
}

/* ---------------------------------------------------------------- Panel */
function renderQuality(q) {
  $("quality-card").hidden = !q;
  if (!q) return;
  $("q-sum").textContent = `${q.grade} · ${q.score}/100`;
  $("quality").innerHTML = Quality.html(q);
}

function render() {
  const r = state.routes[state.active];
  $("result").hidden = !r;
  $("band-card").hidden = !r;
  if (!r) return;
  const st = r.stats;
  $("r-name").textContent = r.name;
  const src = st.source === "lokal" ? `lokale Kartendaten (Stand ${st.osm_date || "?"})` : st.source === "Overpass" ? "Kartendaten live von OpenStreetMap" : `Kartendaten ${st.source}`;
  $("r-sub").textContent = `${r.file} · ${src}` + (st.reconstructed ? " · Straßenverlauf ergänzt" : "");
  renderQuality(r.quality);
  const tiles = [
    [fmt(st.km, st.km < 100 ? 1 : 0) + " km", "Länge"],
    [`${Math.floor(st.time_h)}:${String(Math.round((st.time_h % 1) * 60)).padStart(2, "0")} h`, "Fahrzeit ohne Pausen"],
    [fmt(st.avg_kmh) + " km/h", "Schnitt"],
    [fmt(100 - st.urban_share) + " %", "außerorts"],
    [String(st.towns), "Ortsdurchfahrten"],
    [st.ascent !== null && st.ascent !== undefined ? fmt(st.ascent) + " hm" : fmt(st.longest_free.len, 1) + " km", st.ascent !== null && st.ascent !== undefined ? "Anstieg" : "längster freier Abschnitt"],
  ];
  $("tiles").innerHTML = tiles.map(([b, s]) => `<div class="tile"><b>${esc(b)}</b><small>${esc(s)}</small></div>`).join("");

  $("profiles").innerHTML = Object.entries(PROFILES).map(([k, p]) =>
    `<button role="radio" data-p="${k}" aria-checked="${k === state.profile}">${esc(p.label)}</button>`).join("");
  const sc = routeScore(r, state.profile);
  $("overall").textContent = sc === null ? "–" : Math.round(sc);
  $("ring").style.setProperty("--v", sc ?? 0);
  $("ring").style.setProperty("--c", scaleColor(sc));
  $("profile-text").textContent = `${PROFILES[state.profile].text} ${verdict(sc)}`;

  const w = PROFILES[state.profile].w;
  $("axes").innerHTML = AXES.map((a) => {
    const ax = r.axes[a];
    const v = ax.score;
    return `<div class="axis ${w[a] ? "" : "w0"}" title="${w[a] ? `Gewicht im Profil: ${Math.round(w[a] * 100)} %` : "zählt in diesem Profil nicht"}">
      <span>${esc(ax.label)}</span>
      <span class="track"><span style="width:${v ?? 0}%;background:${scaleColor(v)}"></span></span>
      <span class="val">${v ?? "–"}</span>
      ${ax.note ? `<span class="note">${esc(ax.note)}</span>` : ""}</div>`;
  }).join("");

  $("highlights").innerHTML = r.highlights.map((h) => `<li>${esc(h)}</li>`).join("");
  $("warnings").innerHTML = (r.warnings || []).map((h) => `<li>${esc(h)}</li>`).join("");
  renderList();
  renderCompare();
  scheduleWeather();
  drawRoute();
  drawBand();
}

function verdict(sc) {
  if (sc === null) return "";
  if (sc >= 75) return "Diese Strecke passt richtig gut dazu.";
  if (sc >= 60) return "Passt gut – mit ein paar Kompromissen.";
  if (sc >= 45) return "Teils, teils – ein Blick auf die Abschnitte lohnt.";
  return "Vielleicht gibt es heute eine reizvollere Alternative.";
}

function renderList() {
  const r = state.routes[state.active];
  document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === state.tab));
  let items = [];
  if (state.tab === "notes") {
    items = (r.notes || []).map((n) => [n.km, n.label, n.text, n.lat, n.lon]);
  } else if (state.tab === "sights") {
    items = r.pois.filter((p) => p.group === "sight" && p.score >= 1.5)
      .map((p) => [p.km, p.name || p.label, `${p.label}${p.ele ? ` (${p.ele} m)` : ""} · nach ${hm(p.tmin)} · ${p.off} m abseits${p.wiki ? " · Wikipedia" : ""}`, p.lat, p.lon]);
  } else if (state.tab === "pause") {
    items = r.pois.filter((p) => ["food", "pause", "fuel"].includes(p.group))
      .map((p) => [p.km, p.name || p.label, `${p.label} · nach ${hm(p.tmin)} Fahrzeit · ${p.off} m abseits`, p.lat, p.lon]);
  } else if (state.tab === "towns") {
    items = r.towns.map((t) => [t.km0, t.name, `${fmt(t.km1 - t.km0, 1)} km innerorts`, t.lat, t.lon]);
  } else if (state.tab === "events") {
    items = r.events.map((e) => [e.km, e.label, e.w >= .5 ? "unterbricht den Fluss" : "kurzer Taktwechsel", e.lat, e.lon]);
  } else if (state.tab === "curves") {
    items = r.curves.filter((c) => !c.urban).map((c) => [c.km, CURVE_LABEL[c.cls],
      `${vRange(c)}Radius ≈ ${c.r} m · ${c.angle}°${c.s ? " · Wechselkurve" : ""}${c.rough ? " · holpriger Belag oder schmale Straße" : ""}`, c.lat, c.lon]);
  }
  $("list").innerHTML = items.length
    ? items.map(([km, t, s, la, lo]) => `<li data-ll="${la},${lo}"><span class="km">km ${fmt(km, 1)}</span><span>${esc(t)}<small>${esc(s)}</small></span></li>`).join("")
    : `<li class="empty">${state.tab === "notes" ? "Keine Auffälligkeiten – die Route wirkt stimmig." : "Nichts gefunden."}</li>`;
  const nb = document.querySelector('#tabs button[data-tab="notes"]');
  nb.textContent = `Hinweise${r.notes && r.notes.length ? ` (${r.notes.length})` : ""}`;
}

/* Wetter unterwegs: ~9 Punkte entlang der Tour zur geschaetzten Durchfahrt (Fahrzeit ohne Pausen) */
let wxKey = "", wxData = null, wxTimer = 0, wxReq = null;
function weatherRequest(r) {
  const dep = new Date($("wx-dep").value || WeatherUI.localInput(WeatherUI.defaultDeparture()));
  const line = r.segments.map((sg) => [sg.c[0][0], sg.c[0][1], sg.km0]);
  const last = r.segments[r.segments.length - 1];
  line.push([last.c[last.c.length - 1][0], last.c[last.c.length - 1][1], last.km1]);
  return { rig: false, moto: true, stages: WeatherUI.tourPoints(line, dep.getTime(), r.stats.time_h) };
}
function fillWeather() {
  if (!wxData || !wxReq) return;
  const rows = wxData.stages.map((st, i) => {
    const p = wxReq.stages[i].pts[0];
    return WeatherUI.pointRow(st, { km: p[3], iso: p[2] });
  });
  $("wx-list").innerHTML = rows.map((w) => `<li class="${w.level ? "g-" + w.level : ""}"><span class="km">km ${fmt(w.km)}</span>`
    + `<span>${esc(w.text)}<small>${esc(w.time ? "gegen " + w.time : "")}${w.level ? " · Böen – Achtung Seitenwind" : ""}</small></span></li>`).join("");
  $("wx-warns").innerHTML = WeatherUI.warnRows(WeatherUI.uniqueWarnings(wxData.stages))
    .map((x) => `<p class="wx-warn ${x.cls}" title="${esc(x.source)}">${esc(x.text)}</p>`).join("");
  const err = Object.values(wxData.errors || {});
  $("wx-note").textContent = `Zeiten nach reiner Fahrzeit, ohne Pausen.${err.length ? " " + err.join(" · ") + "." : ""} ${wxData.attribution || ""}`;
}
function scheduleWeather() {
  const r = state.routes[state.active];
  if (!r || !r.segments || !r.segments.length) return;
  if (!$("wx-dep").value) $("wx-dep").value = WeatherUI.localInput(WeatherUI.defaultDeparture());
  const req = weatherRequest(r), key = JSON.stringify(req);
  if (key === wxKey) { fillWeather(); return; }
  $("wx-list").innerHTML = '<li class="empty">Wetter wird geladen …</li>';
  $("wx-warns").innerHTML = "";
  clearTimeout(wxTimer);
  wxTimer = setTimeout(async () => {
    try {
      const res = await fetch("/api/weather", { method: "POST", headers: { "Content-Type": "application/json" }, body: key });
      if (!res.ok) throw new Error(res.status);
      wxData = await res.json(); wxKey = key; wxReq = req;
    } catch { wxData = { stages: [], errors: { all: "Wetter gerade nicht abrufbar" }, attribution: "" }; wxReq = req; wxKey = ""; }
    if (JSON.stringify(weatherRequest(state.routes[state.active])) === key) fillWeather();   // inzwischen andere Route? dann nicht
  }, 500);
}
$("wx-dep").addEventListener("change", scheduleWeather);

function renderCompare() {
  const n = state.routes.length;
  $("compare-card").hidden = n < 2;
  if (n < 2) return;
  const rows = [
    ["Länge", (r) => r.stats.km, (v) => fmt(v, 0) + " km", null],
    ["Fahrzeit", (r) => r.stats.time_h, (v) => `${Math.floor(v)}:${String(Math.round((v % 1) * 60)).padStart(2, "0")} h`, null],
    ...Object.keys(PROFILES).map((k) => [`Passung ${PROFILES[k].label}`, (r) => routeScore(r, k), (v) => v === null ? "–" : Math.round(v), 1]),
    ...AXES.map((a) => [state.routes[0].axes[a].label, (r) => r.axes[a].score, (v) => v ?? "–", 1]),
    ["Ortsdurchfahrten", (r) => r.stats.towns, (v) => v, -1],
    ["Hinweise", (r) => (r.notes || []).length, (v) => v, -1],
    ["Unterbrechungen/100 km", (r) => r.stats.events_per_100km, (v) => fmt(v, 0), -1],
  ];
  const head = `<thead><tr><th></th>${state.routes.map((r, i) => `<th data-i="${i}" class="${i === state.active ? "active" : ""}" title="${esc(r.name)}">`
    + `<span class="cmp-name"><i class="cmp-dot" style="background:${CompareUI.colorOf(i)}"></i>${esc(CompareUI.colName(r.name, i))}</span>`
    + `<button type="button" class="cmp-x" data-x="${i}" title="Aus dem Vergleich nehmen" aria-label="${esc(r.name)} aus dem Vergleich nehmen">✕</button></th>`).join("")}</tr></thead>`;
  const body = rows.map(([label, get, show, better]) => {
    const vals = state.routes.map(get);
    let best = null;
    if (better) {
      const nums = vals.filter((v) => v !== null && v !== undefined);
      if (nums.length > 1) best = better > 0 ? Math.max(...nums) : Math.min(...nums);
    }
    return `<tr><td>${esc(label)}</td>${vals.map((v) => `<td class="${best !== null && v === best ? "best" : ""}">${esc(show(v))}</td>`).join("")}</tr>`;
  }).join("");
  $("compare").innerHTML = head + `<tbody>${body}</tbody>`;
}

/* ---------------------------------------------------------------- Streckenband */
function drawBand() {
  const r = state.routes[state.active];
  const W = 1000, rowH = 16, gap = 6, left = 86, top = 6;
  const H = top + BAND_ROWS.length * (rowH + gap) + 34;
  const km = r.stats.km;
  const x = (k) => left + (k / km) * (W - left - 8);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Streckenband">`;
  BAND_ROWS.forEach(([key, label], ri) => {
    const y = top + ri * (rowH + gap);
    s += `<text x="0" y="${y + rowH - 4}">${label}</text>`;
    for (const seg of r.segments) {
      const v = seg.sc[key];
      s += `<rect x="${x(seg.km0).toFixed(1)}" y="${y}" width="${Math.max(x(seg.km1) - x(seg.km0) + .4, 1).toFixed(1)}" height="${rowH}" fill="${scaleColor(v)}"/>`;
    }
  });
  const yE = top + BAND_ROWS.length * (rowH + gap);
  for (const e of r.events) if (e.w >= .5) s += `<line x1="${x(e.km)}" x2="${x(e.km)}" y1="${yE}" y2="${yE + 7}" stroke="${cssVar("--ink")}" stroke-width="1"/>`;
  s += `<text x="0" y="${yE + 8}">Stopps</text>`;
  const stepKm = km > 200 ? 50 : km > 80 ? 20 : km > 30 ? 10 : 5;
  for (let k = 0; k <= km; k += stepKm) s += `<text x="${x(k)}" y="${H - 4}" text-anchor="middle">${k}</text>`;
  s += `<line class="cursor" id="band-cursor" x1="-10" x2="-10" y1="${top}" y2="${yE + 8}"/>`;
  s += `<rect id="band-hit" x="${left}" y="0" width="${W - left - 8}" height="${H}" fill="transparent"/></svg>`;
  $("band").innerHTML = s;
  const svg = $("band").querySelector("svg");
  const toKm = (ev) => {
    const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    return Math.max(0, Math.min(km, ((p.x - left) / (W - left - 8)) * km));
  };
  const segAt = (k) => r.segments.find((sg) => k >= sg.km0 && k <= sg.km1) || r.segments[r.segments.length - 1];
  $("band-hit").addEventListener("mousemove", (ev) => {
    const k = toKm(ev);
    const c = $("band-cursor"); c.setAttribute("x1", x(k)); c.setAttribute("x2", x(k));
    const sg = segAt(k);
    moveCursor(sg.c[Math.floor(((k - sg.km0) / Math.max(sg.km1 - sg.km0, .01)) * (sg.c.length - 1))]);
  });
  $("band-hit").addEventListener("click", (ev) => {
    const sg = segAt(toKm(ev));
    map.fitBounds(L.latLngBounds(sg.c).pad(2));
  });
}

/* ---------------------------------------------------------------- Upload + Jobs */
async function submit(promise) {
  $("error").hidden = true;
  $("missing").hidden = true;
  $("progress").hidden = false;
  setProgress(1, "wird hochgeladen …");
  Analysis.begin();
  try {
    const res = await promise;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
    Analysis.setJob(data.job);
    await poll(data.job);
  } catch (e) {
    $("progress").hidden = true;
    if (!(e instanceof Analysis.Stopped)) { $("error").textContent = e.message; $("error").hidden = false; }
  } finally {
    Analysis.end();
  }
}

async function poll(id) {
  const t0 = Date.now();
  let down = 0;
  for (;;) {
    // erst zuegig, nach 15 s gemaechlicher nachfragen (Live-Daten dauern)
    await new Promise((r) => setTimeout(r, Date.now() - t0 < 15000 ? 900 : 3000));
    if (Analysis.cancelled) throw new Analysis.Stopped();
    // Server startet gerade neu (Update): bis zu 3 min weiter nachfragen statt abzubrechen
    const res = await fetch(`/api/jobs/${id}`).catch(() => null);
    if (!res || res.status >= 502) {
      down = down || Date.now();
      if (Date.now() - down > 180000) throw new Error("Der Server ist gerade nicht erreichbar. Bitte gleich nochmal versuchen.");
      $("progress-msg").textContent = "Server startet gerade neu (Update) – einen Moment …";
      continue;
    }
    down = 0;
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.detail || "Analyse nicht mehr verfügbar.");
    if (j.state === "cancelled" || Analysis.cancelled) throw new Analysis.Stopped();
    if (j.state === "error") {
      showMissing(j.missing);
      if (j.missing && j.missing.length && /Kartendaten gerade nicht abrufbar/.test(j.msg)) {
        throw new Error("Die Live-Kartendaten von OpenStreetMap sind gerade nicht erreichbar. Später nochmal versuchen – oder oben die Region aufnehmen, dann klappt es dauerhaft und schnell.");
      }
      throw new Error(j.msg);
    }
    if (j.state === "done") {
      showMissing(j.result.missing);
      setProgress(100, "fertig");
      setTimeout(() => { $("progress").hidden = true; }, 600);
      addRoute(j.result);
      return;
    }
    if (j.missing && $("missing").hidden) showMissing(j.missing);
    setProgress(j.progress, QueueText.label(j));
  }
}

const clock = (epoch) => new Date(epoch * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const dayClock = (epoch) => {
  const d = new Date(epoch * 1000), today = new Date();
  return d.toDateString() === today.toDateString() ? `${clock(epoch)} Uhr` : `${d.toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "numeric" })}, ${clock(epoch)} Uhr`;
};

// Teile der Route ohne lokale Kartendaten: Hinweis + Region nachbestellen
function showMissing(list) {
  const box = $("missing");
  list = (list || []).filter((m) => m.share > 0);
  if (!list.length) { box.hidden = true; return; }
  const rows = list.map((m) => {
    const meta = [m.share ? `${m.share} % der Route` : "", m.size_mb ? `${m.size_mb} MB` : "",
      m.est_min ? `Aufbau ca. ${m.est_min < 90 ? m.est_min + " Min." : (m.est_min / 60).toFixed(1).replace(".", ",") + " Std."}` : ""]
      .filter(Boolean).join(" · ");
    const act = !m.region ? "" : m.queued
      ? `<span class="ok">eingeplant – fertig ca. ${dayClock(m.eta)}</span>`
      : `<button type="button" data-region="${esc(m.region)}">Region aufnehmen</button>`;
    return `<div class="reg"><b>${esc(m.name)}</b><small>${esc(meta)}</small>${act}</div>`;
  }).join("");
  box.innerHTML = `<p>Ein Teil der Route liegt außerhalb der lokalen Kartendaten. Dafür werden Live-Daten von OpenStreetMap geholt – langsam und unter Last unzuverlässig. Mit der Region im lokalen Bestand geht es in Sekunden:</p>${rows}`;
  box.hidden = false;
}

$("missing").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-region]");
  if (!b) return;
  b.disabled = true; b.textContent = "wird eingeplant …";
  try {
    const res = await fetch("/api/regions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ region: b.dataset.region }) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    const span = document.createElement("span");
    span.className = "ok";
    span.textContent = d.eta ? `eingeplant – fertig ca. ${dayClock(d.eta)}, dann die Route nochmal analysieren` : d.msg;
    b.replaceWith(span);
    loadInfo();
  } catch (err) {
    b.disabled = false; b.textContent = "Region aufnehmen";
    alert(err.message);
  }
});

function setProgress(p, msg) {
  $("bar").style.width = `${Math.max(2, p)}%`;
  $("progress-msg").textContent = msg;
}

function addRoute(r) {
  $("cov-toggle").checked = false; map.removeLayer(covLayer);
  state.routes.push(r);
  state.active = state.routes.length - 1;
  render();
  ticker.route(r);
  fitRoutes();
  if (window.matchMedia("(max-width: 900px)").matches && !demoParam) $("result-card").scrollIntoView({ behavior: "smooth" });
}

function uploadFile(f) {
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f, f.name);
  submit(fetch("/api/analyze", { method: "POST", body: fd }));
}

/* ---------------------------------------------------------------- Events */
$("file").addEventListener("change", (e) => { uploadFile(e.target.files[0]); e.target.value = ""; });
const drop = $("drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => uploadFile(e.dataTransfer.files[0]));
document.querySelectorAll("[data-demo]").forEach((b) =>
  b.addEventListener("click", () => submit(fetch(`/api/demo/${b.dataset.demo}`, { method: "POST" }))));
$("profiles").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-p]");
  if (!b) return;
  state.profile = b.dataset.p;
  try { localStorage.setItem("ra.profile", state.profile); } catch { /* privat */ }
  render();
  ticker.route(state.routes[state.active]);
});
$("tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  state.tab = b.dataset.tab;
  renderList();
});
$("list").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-ll]");
  if (!li) return;
  const [la, lo] = li.dataset.ll.split(",").map(Number);
  map.setView([la, lo], 15);
  moveCursor([la, lo]);
  if (window.matchMedia("(max-width: 900px)").matches) $("map").scrollIntoView({ behavior: "smooth" });
});
function removeRoute(i) {
  state.active = CompareUI.nextActive(state.active, i, state.routes.length);
  state.routes.splice(i, 1);
  render();
  ticker.route(state.routes[state.active]);
  fitRoutes();
}

/** Karte auf alle Touren im Vergleich einpassen, bei nur einer auf diese. */
function fitRoutes() {
  LayoutUI.fitWhenSettled(map, L.latLngBounds(state.routes.flatMap((r) => r.segments.flatMap((s) => s.c))));
}

$("compare").addEventListener("click", (e) => {
  const x = e.target.closest("button[data-x]");
  if (x) { removeRoute(Number(x.dataset.x)); return; }
  const th = e.target.closest("th[data-i]");
  if (th) selectRoute(Number(th.dataset.i), true);
});

function selectRoute(i, fit) {
  state.active = i;
  render();
  ticker.route(state.routes[state.active]);
  if (fit) fitRoutes();
}
$("layer").addEventListener("change", (e) => { state.layer = e.target.value; drawRoute(); });
$("toggles").addEventListener("change", syncToggles);
LayoutUI.mountLayerButton(map, $("toggles"), "ride.layers", syncToggles);
// Hell/Dunkel-Schalter (Standard dunkel, s. theme.js)
function syncThemeButton() {
  const dark = document.documentElement.getAttribute("data-theme") !== "light";
  const b = $("theme-toggle");
  b.querySelector(".ico").textContent = dark ? "☀️" : "🌙";
  b.querySelector(".lbl").textContent = dark ? "Hell" : "Dunkel";
  b.setAttribute("aria-label", dark ? "Helles Design" : "Dunkles Design");
}
$("theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("ra.theme", next); } catch { /* privat */ }
  syncThemeButton();
  if (state.active >= 0) render(); else drawLegend();
});
syncThemeButton();

// Kartendaten: auf der Startseite nur eine Zeile, Laenderliste und Details unter Funktionen
function coverageHint(i, intro) {
  const cs = (i.osm && i.osm.countries) || [];
  if (!cs.length) return esc(intro) + `Kartendaten kommen live von OpenStreetMap (dauert etwas). <a href="/features.html#karten">Mehr</a>`;
  return esc(intro) + `Lokale Kartendaten für ${cs.length} Länder – <a href="/features.html#karten">welche?</a>`;
}

function loadInfo() {
  return fetch("/api/info").then((r) => r.json()).then((i) => {
    const names = (i.osm && i.osm.names) || {};
    const nm = (r) => names[r] || (i.queue || []).find((q) => q.region === r)?.name || r.split("/").pop();
    const have = i.osm ? i.osm.regions.map(nm) : [];
    const list = (a) => (a.length > 1 ? a.slice(0, -1).join(", ") + " und " + a[a.length - 1] : a[0] || "");
    const q = i.queue || [];
    const qtxt = q.length ? " · in Arbeit: " + q.map((e) => `${e.name} (fertig ca. ${dayClock(e.eta)})`).join(", ") : "";
    $("ver").textContent = `RideAnalyzer v${i.version} · Stand ${i.stamp}`;
    const cs = ((i.osm && i.osm.countries) || []).map((c) => c.name);
    ticker.regions(cs.length ? list(cs) : "");
    $("osm-state").textContent = (have.length ? `lokale Daten: ${cs.length} Länder, ${have.length} Regionen (${(i.osm.built || "").slice(0, 10)})` : "nur Live-Kartendaten") + qtxt;
    $("limits").innerHTML = coverageHint(i, `Bis ${i.max_km} km, max. ${i.max_mb} MB. `)
      + (q.length ? ` Gerade in Arbeit: ${esc(q.map((e) => e.name).join(", "))}.` : "");
  }).catch(() => {});
}
loadInfo();
setInterval(loadInfo, 120000);
drawLegend();
// ?demo=barnim laedt die Beispieltour direkt (zum Teilen)
const demoParam = new URLSearchParams(location.search).get("demo");
if (demoParam && /^[a-z0-9]+$/i.test(demoParam)) submit(fetch(`/api/demo/${demoParam}`, { method: "POST" }));

/* ---------------------------------------------------------------- Laufband */
// Text faehrt ueber die "Strasse" im Huegel. Ohne Route: was die App kann; mit Route:
// deren Eckdaten. Bei "Bewegung reduzieren" steht der Text still.
const ticker = (() => {
  const tp = $("ridgeTextPath");
  const road = document.getElementById("ridgeRoad");
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  const SEP = "   ·   ";
  const SPEED = 55;   // Einheiten pro Sekunde (viewBox)
  let regions = "";
  let pos = null, last = 0, len = 0, roadLen = road.getTotalLength();

  const base = () => [
    "Der Blick macht die Tour.",
    "GPX ablegen – Fahrfluss, Kurven, Orte, Belag, Landschaft und Sehenswertes auf einen Blick",
    "Spitzkehren, kurze Schlenker und holprige Kurvenpassagen werden markiert",
    "Pausenrhythmus: Einkehr mit Fahrzeit, nicht nur mit Kilometern",
    "Mehrere Routen laden – der Vergleich steht nebeneinander",
    "Kurviger- oder Calimoto-Export? Ausgedünnte Routen werden entlang des Straßennetzes ergänzt",
    ...(regions ? [`Lokale Kartendaten: ${regions}`] : []),
    "Der Mensch fährt – RideAnalyzer liefert den Blick",
  ];

  function setText(parts) {
    tp.textContent = parts.join(SEP) + SEP;
    len = tp.getComputedTextLength();
    pos = 320;   // gleich sichtbar am linken Rand beginnen (Pfad startet links ausserhalb)
  }

  function frame(t) {
    if (pos !== null && !still.matches) {
      const dt = last ? Math.min((t - last) / 1000, 0.1) : 0;
      pos -= SPEED * dt;
      if (pos < -len) pos = roadLen;
      tp.setAttribute("startOffset", pos.toFixed(1));
    }
    last = t;
    requestAnimationFrame(frame);
  }

  function forRoute(r) {
    const st = r.stats;
    const h = Math.floor(st.time_h), m = Math.round((st.time_h % 1) * 60);
    const parts = [`${r.name}: ${fmt(st.km, 0)} km, ca. ${h}:${String(m).padStart(2, "0")} h Fahrzeit`];
    const sc = routeScore(r, state.profile);
    if (sc !== null) parts.push(`Passung ${PROFILES[state.profile].label}: ${Math.round(sc)} von 100`);
    parts.push(...r.highlights.slice(0, 5).map((x) => x.replace(/\.$/, "")));
    if (r.notes && r.notes.length) parts.push(`${r.notes.length} Hinweis${r.notes.length > 1 ? "e" : ""} – ein Blick lohnt`);
    setText(parts);
  }

  still.addEventListener("change", () => { pos = still.matches ? 20 : roadLen; tp.setAttribute("startOffset", pos); });
  setText(base());
  requestAnimationFrame(frame);
  return {
    regions(list) { regions = list; if (state.active < 0) setText(base()); },
    route(r) { if (r) forRoute(r); else setText(base()); },
  };
})();
loadCoverage(!demoParam).then(() => { if (state.active < 0 && $("cov-toggle").checked) $("legend").innerHTML = AGE_LEGEND; });

/* ---------------------------------------------------------------- Karten verschiebbar */
// Wie im Rig (dock.js): ⠿ ziehen, ↑/↓, ⇄ links ↔ unter der Karte, ▾ einklappen. Anordnung im Browser
// („ride.layout“) und am Login (prefs „ride_ui“, der neuere Stand gewinnt). ↺ erscheint nur, wenn etwas verstellt ist.
DockKit.mount({ left: "dock-left", right: "dock-right", def: DockKit.DEFAULTS.ride, key: "ride.layout", part: "ride_ui",
  result: "result", reset: "dock-reset", resetAuto: true, map: () => map });

/* ---------------------------------------------------------------- Hilfe */
$("help-btn").addEventListener("click", () => $("help").showModal());
$("help").addEventListener("click", (e) => { if (e.target === $("help")) $("help").close(); });
try {
  if (!localStorage.getItem("ra.helpSeen") && !demoParam) { $("help").showModal(); localStorage.setItem("ra.helpSeen", "1"); }
} catch { /* privat */ }
