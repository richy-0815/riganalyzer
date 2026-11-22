/* „🅿 Uebernachten & Versorgen“ (RigAnalyzer): Stell-/Campingplaetze und Ver-/Entsorgung entlang
   der Route filtern – Filter-Chips, Liste nach km, Karte zeigt nur die Gefilterten, Details im Popup.
   Laeuft nach rig.js und nutzt dessen route/trip/veh/layers/pin/esc/fmt. */
const CAMP_KEY = "rig.campfilter";
const campF = (() => { try { return JSON.parse(localStorage.getItem(CAMP_KEY) || "{}") || {}; } catch { return {}; } })();
const campMarkers = new Map();   // osm-id -> Marker je Ebene (Liste oeffnet das Popup)

const CAMP_GROUPS = [
  ["Ver-/Entsorgung", [["dump", "🚽 Entsorgung"], ["water", "🚰 Wasser"], ["power", "🔌 Strom"]]],
  ["Größe & Gespann", [["fits", "🚐 passt fürs Fahrzeug"], ["lenKnown", "📏 Länge geprüft"]]],
  ["Kosten & Zeiten", [["free", "💶 kostenlos"], ["openNow", "🕒 jetzt geöffnet"], ["yearRound", "📅 ganzjährig"]]],
  ["Komfort", [["shower", "🚿 Duschen"], ["toilets", "🚻 WC"], ["wifi", "📶 WLAN"], ["dog", "🐕 Hunde"]]],
];
const WATER_KINDS = { public: ["public"], guests: ["public", "guests"], tap: ["public", "guests", "tap"] };
const WATER_TEXT = { guests: "nur für Gäste des Platzes", tap: "Trinkwasserhahn – wahrscheinlich kein Schlauchanschluss",
  unknown: "Zugang unbekannt (Kartendaten werden aktualisiert)" };

function saveCampF() { try { localStorage.setItem(CAMP_KEY, JSON.stringify(campF)); } catch { /* nur Komfort */ } }
const campKind = (p) => (p.flags && p.flags.kind === "caravan_site" ? "Stellplatz" : p.flags && p.flags.kind === "camp_site" ? "Campingplatz" : p.label);
const campName = (p) => p.name || `${campKind(p)} bei km ${fmt(p.km, 0)}`;
const isStation = (p) => ["Entsorgung", "Frischwasser", "Trinkwasser"].includes(p.label);

function campFilter() {
  return { ...campF, waterKinds: WATER_KINDS[campF.waterSel] || WATER_KINDS.public, nearStage: !!(trip && campF.nearStage) };
}
function campsShown(r) { return CampFilter.filterCamps(r.pois || [], campFilter(), veh, new Date()); }

function campSymbols(f) {
  return [f.dump && "🚽", f.water && "🚰", f.power && "🔌", f.shower && "🚿", f.toilets && "🚻", f.wifi && "📶",
    (f.dog === "yes" || f.dog === "leashed") && "🐕", f.fee === false && "💶 0"].filter(Boolean).join(" ");
}
// Ampel „passt fuers Fahrzeug“: gruen bekannt+passt, hellgruen wahrscheinlich (Luftbild/Website), gelb unbekannt,
// orange wahrscheinlich zu klein, rot zu gross
const FIT_LIGHT = { ok: ["green", "passt (Länge/Gewicht bekannt)"], likely: ["lgreen", "passt wahrscheinlich (Länge geprüft, s. Details)"],
  likely_no: ["orange", "wahrscheinlich zu kurz für euer Fahrzeug (s. Details)"], no: ["red", "zu groß/schwer für euer Fahrzeug"],
  unknown: ["amber", "Länge/Gewicht unbekannt"] };
function campLight(p) {
  if (isStation(p)) return "";
  const [cls, tip] = FIT_LIGHT[CampFilter.fitsRig(p.flags || {}, veh)] || FIT_LIGHT.unknown;
  return `<span class="sev ${cls}" title="${tip}"></span>`;
}

/* Stellplatz-Laenge: fuer die Plaetze der Route beim Server nachfragen – Angabe aus Beschreibung/Website
   (bestaetigt) oder Schaetzung aus dem Luftbild (wahrscheinlich). Noch nicht ausgewertete Plaetze rechnet der Server
   im Hintergrund; solange fragt die Seite alle 20 s nach (hoechstens 30 Mal). */
const LAENGE_KEY = (p) => (p.osm && /^(node|way|relation)\/\d+$|^dt:/.test(p.osm) ? p.osm : "");
let laengeFor = null, laengeTimer = null, laengeTries = 0, laengeStatus = null;
// globale Funktion, wird auch von rig.js (anderes Skript) genutzt
function ensureLaenge(again = false) {
  const base = combined || route;
  if (!base || (laengeFor === base && !again)) return;
  if (laengeFor !== base) { laengeTries = 0; clearTimeout(laengeTimer); }
  laengeFor = base;
  const camps = base.pois.filter((p) => p.group === "camp" && !isStation(p) && LAENGE_KEY(p) && !(p.laenge && p.laenge.state === "done"));
  if (!camps.length) return;
  // Stellplaetze zuerst, dann nah an der Strecke – der Server arbeitet in dieser Reihenfolge
  camps.sort((a, b) => ((a.flags || {}).kind === "caravan_site" ? 0 : 1) - ((b.flags || {}).kind === "caravan_site" ? 0 : 1) || (a.off || 0) - (b.off || 0));
  fetch("/api/camps/laenge", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ places: camps.slice(0, 400).map((p) => [LAENGE_KEY(p), p.lat, p.lon]) }) })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (!j || laengeFor !== base) return;
      laengeStatus = j.status || null;
      let pending = 0;
      for (const p of camps) {
        const x = j.places[LAENGE_KEY(p)];
        if (!x) continue;
        p.laenge = x;
        if (x.state === "pending") pending++;
        if (x.state === "done") {
          p.flags = p.flags || {};
          if (x.verdict) p.flags.laenge = x.verdict;
          if (x.trailer === false) p.flags.trailer = false;
        }
      }
      if (route) renderAll();
      if (pending && ++laengeTries < 30) laengeTimer = setTimeout(() => ensureLaenge(true), 20000);
    }).catch(() => { /* Zusatz */ });
}
const fmtLen = (v) => (v.kind === "none" ? "ohne Längenbegrenzung"
  : `${v.kind === "min" ? "auch für " : v.level === "likely" ? "ca. " : "bis "}${fmt(v.m, 1)} m`);
function laengeBox(p) {
  const x = p.laenge;
  if (!x) return "";
  if (x.state === "pending") {
    const st = laengeStatus;
    return `<div class="lenbox muted">📏 Länge wird geprüft (Website${st && st.luftbild ? ", Luftbild" : ""}) …</div>`;
  }
  if (x.state !== "done") return "";
  const v = x.verdict, lb = x.luft, rows = [];
  if (v) {
    rows.push(`📏 <b>${fmtLen(v)}</b> · ${v.level === "confirmed" ? "laut" : "geschätzt aus"} ${esc(v.src)}`
      + `${x.url && v.src === "Website" ? ` (<a href="${esc(x.url)}" target="_blank" rel="noopener">Seite ↗</a>)` : ""}`);
    if (v.why) rows.push(`<i>„${esc(v.why)}“</i>`);
  } else rows.push("📏 Länge: keine Angabe gefunden");
  if (x.trailer === false) rows.push("<b>Gespanne laut Text nicht erlaubt</b>");
  if (lb) {
    rows.push(`<small>🛰 gemessen im Luftbild: längste freie Bahn ${fmt(lb.stellbahn_m, 0)} m · freie Wendefläche ${fmt(lb.rangier_m, 0)} × ${fmt(lb.rangier_m, 0)} m`
      + ` · ${Math.round((lb.offen || 0) * 100)} % der Fläche frei</small>`);
    if (lb.img) rows.push(`<a href="/api/camps/laenge/bild?key=${encodeURIComponent(LAENGE_KEY(p))}" target="_blank" rel="noopener">Gemessenes Luftbild ansehen ↗</a>`
      + ` <small>${esc(lb.src)}${lb.date ? `, Aufnahme ${esc(lb.date.split("-").reverse().join("."))}` : ""}</small>`);
  }
  const g = x.geo || {};
  if (g.stellflaechen) rows.push(`<small>OSM: ${g.stellflaechen} Stellflächen, längste ${fmt(g.stellflaeche_max_m, 1)} m</small>`);
  if (x.zufahrt && x.zufahrt.length) {
    rows.push(`<small>Zufahrt: ${x.zufahrt.slice(0, 3).map((h) => `${esc(h.was)}${h.wert ? ` ${fmt(h.wert, 1)} m` : ""} (${h.abstand} m entfernt)`).join(" · ")}</small>`);
  }
  return `<div class="lenbox">${rows.join("<br>")}</div>`;
}

// Zusatzinfos aus OSM: Untergrund, Reservierung, Sterne, Beschreibung, zuletzt geprueft …
const SURFACE = { grass: "Wiese", gravel: "Schotter", fine_gravel: "Feinschotter", asphalt: "Asphalt", paved: "befestigt",
  compacted: "verdichtet", sand: "Sand", dirt: "Erde", ground: "Erde", paving_stones: "Pflaster", concrete: "Beton", unpaved: "unbefestigt" };
const RESERVATION = { required: "Reservierung nötig", recommended: "Reservierung empfohlen", yes: "Reservierung möglich", no: "ohne Reservierung" };
// park4night: keine Schnittstelle – nur die Karte dort an dieser Stelle oeffnen (Bewertungen, Fotos)
const p4nHref = (lat, lon) => `https://park4night.com/de/search?lat=${(+lat).toFixed(5)}&lng=${(+lon).toFixed(5)}&z=15`;

/* Plaetze der franzoesischen Tourismusaemter (DATAtourisme): einmal je Route beim Server nachfragen –
   OSM-Plaetze bekommen p.fr (offizielle Sterne, Beschreibung, Website, Stand), in OSM fehlende kommen dazu. */
const FR_BOX = [41.3, 51.2, -5.3, 9.7];
const inFR = (la, lo) => la >= FR_BOX[0] && la <= FR_BOX[1] && lo >= FR_BOX[2] && lo <= FR_BOX[3];
let frFor = null;
function frLine(r) {
  const out = [];
  for (const g of r.segments) {
    const n = g.c.length, len = g.km1 - g.km0, step = Math.max(1, Math.round(n / Math.max(1, len / 1.5)));
    for (let i = 0; i < n; i += step) {
      const [la, lo] = g.c[i];
      if (inFR(la, lo)) out.push([la, lo, +(g.km0 + len * i / Math.max(1, n - 1)).toFixed(2)]);
    }
  }
  return out.slice(0, 5000);
}
// biome-ignore lint/correctness/noUnusedVariables: globale Funktion, wird von planner.js/rig.js (andere Skripte) genutzt
function ensureFrCamps() {
  const base = combined || route;
  ensureReviews();
  if (!base || frFor === base) return;
  frFor = base;
  const pois = base.pois.filter((p) => p.group === "camp" && !isStation(p) && p.osm && inFR(p.lat, p.lon))
    .map((p) => [p.osm, p.lat, p.lon, p.name || ""]).slice(0, 3000);
  const line = frLine(base);
  if (!pois.length && !line.length) return;
  fetch("/api/camps/fr", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pois, line }) })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (!j || frFor !== base) return;
      base.pois.forEach((p) => { if (p.osm && j.match[p.osm]) p.fr = j.match[p.osm]; });
      const have = new Set(base.pois.map((p) => p.osm));
      for (const e of j.extra || []) {
        const osm = `dt:${e.id}`;
        if (have.has(osm)) continue;
        base.pois.push({ group: "camp", label: e.k === "camping" ? "Campingplatz" : "Stellplatz", name: e.n, lat: e.lat, lon: e.lon,
          km: e.km, off: e.off, osm, fr: e, flags: { kind: e.k === "camping" ? "camp_site" : "caravan_site" } });   // Website steht im Tourismusamt-Kasten
      }
      base.pois.sort((a, b) => a.km - b.km);
      ensureReviews(true);   // neue Plaetze vom Tourismusamt auch nach Bewertungen fragen
      ensureLaenge(true);    // … und nach ihrer Laenge
      if (route) renderAll();
    }).catch(() => { /* Zusatz */ });
}
/* Eigene Bewertungen: Kurzfassung je Platz der Route (p.rev), Dialog zum Lesen und Bewerten */
const revKey = (p) => (p.osm && /^(node|way|relation)\/|^dt:/.test(p.osm) ? p.osm : `ll:${(+p.lat).toFixed(5)},${(+p.lon).toFixed(5)}`);
let revFor = null;
function ensureReviews(force = false) {
  const base = combined || route;
  if (!base || (revFor === base && !force)) return;
  revFor = base;
  const camps = base.pois.filter((p) => p.group === "camp" && !isStation(p));
  if (!camps.length) return;
  fetch("/api/reviews/lookup", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ places: camps.slice(0, 3000).map((p) => [revKey(p), p.lat, p.lon]) }) })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (!j || revFor !== base) return;
      camps.forEach((p) => { p.rev = j.places[revKey(p)] || null; });
      if (route) renderAll();
    }).catch(() => { /* Zusatz */ });
}
const starStr = (v) => "★".repeat(Math.round(v)) + "☆".repeat(5 - Math.round(v));
const dDate = (s) => (s ? s.split("-").reverse().join(".") : "");
const FIT_TEXT = { ok: "🚐 passte gut", eng: "🚐 wurde eng", nein: "🚐 passte nicht" };
function revLine(p) {
  if (!p.rev) return `<button type="button" class="revbtn" data-rev="${esc(revKey(p))}">✍ Als Erste/r bewerten</button>`;
  const l = p.rev.last;
  return `<div class="revbox"><b>⭐ ${fmt(p.rev.avg, 1)}</b> aus ${p.rev.n} eigenen Bewertung${p.rev.n > 1 ? "en" : ""}`
    + `<br><small>${starStr(l.stars)} ${esc(l.user)}${l.night ? ` · Nacht ${dDate(l.night)}` : ""}${l.price != null ? ` · ${fmt(l.price, 2)} €` : ""}${l.fit ? ` · ${FIT_TEXT[l.fit]}` : ""}</small>`
    + `${l.text ? `<br><i>„${esc(l.text)}“</i>` : ""}<br><button type="button" class="revbtn" data-rev="${esc(revKey(p))}">Alle ansehen / bewerten</button></div>`;
}

// Dialog: alle Bewertungen eines Platzes + eigene abgeben/aendern/loeschen
let revDlg = null, revPoi = null;
function openReviews(p) {
  revPoi = p;
  if (!revDlg) {
    revDlg = document.createElement("dialog");
    revDlg.className = "revdlg";
    document.body.appendChild(revDlg);
    revDlg.addEventListener("click", (e) => { if (e.target === revDlg) revDlg.close(); });
    revDlg.addEventListener("click", onRevClick);
  }
  revDlg.innerHTML = `<h3>${esc(campName(p))}</h3><p class="hint">lädt …</p>`;
  revDlg.showModal();
  const q = new URLSearchParams({ key: revKey(p), lat: p.lat, lon: p.lon });
  fetch(`/api/reviews?${q}`).then((r) => r.json()).then((j) => renderRevDlg(p, j)).catch(() => {
    revDlg.innerHTML = `<h3>${esc(campName(p))}</h3><p class="hint">Bewertungen gerade nicht erreichbar.</p><button type="button" data-rclose>Schließen</button>`;
  });
}
function renderRevDlg(p, j) {
  const m = j.mine || {}, today = new Date().toISOString().slice(0, 10);
  const list = j.reviews.length ? j.reviews.map((r) => `<div class="revitem"><b>${starStr(r.stars)}</b> ${esc(r.user)} · ${r.night ? `Nacht ${dDate(r.night)}` : new Date(r.at * 1000).toLocaleDateString("de-DE")}`
    + `${r.price != null ? ` · ${fmt(r.price, 2)} €` : ""}${r.fit ? ` · ${FIT_TEXT[r.fit]}` : ""}${r.veh && r.veh.length ? ` <small>(${fmt(r.veh.length, 1)} m lang${r.veh.height ? `, ${fmt(r.veh.height, 1)} m hoch` : ""})</small>` : ""}`
    + `${r.text ? `<br>${esc(r.text).replace(/\n/g, "<br>")}` : ""}`
    + `${j.admin && r.user !== j.me ? ` <button type="button" class="linkbtn" data-rdel="${esc(r.user)}" data-key="${esc(r.place)}">löschen (Admin)</button>` : ""}</div>`).join("")
    : `<p class="hint">Noch keine Bewertung – seid die Ersten.</p>`;
  revDlg.innerHTML = `<h3>${esc(campName(p))}</h3>${list}
    <form class="revform" method="dialog"><h4>${j.mine ? "Eure Bewertung ändern" : "Eure Bewertung"}</h4>
      <div class="revstars" role="radiogroup" aria-label="Sterne">${[1, 2, 3, 4, 5].map((n) => `<button type="button" data-star="${n}" aria-pressed="${(m.stars || 0) >= n}">★</button>`).join("")}</div>
      <label>Nacht <input type="date" name="night" value="${esc(m.night || today)}"></label>
      <label>Preis pro Nacht <span><input type="text" inputmode="decimal" name="price" value="${m.price != null ? String(m.price).replace(".", ",") : ""}"> €</span></label>
      <div class="revfit">${["ok", "eng", "nein"].map((f) => `<button type="button" data-fit="${f}" aria-pressed="${m.fit === f}">${FIT_TEXT[f]}</button>`).join("")}</div>
      <textarea name="text" rows="4" maxlength="800" placeholder="Wie war's? Untergrund, Lärm, Zufahrt, Ver-/Entsorgung, Tipps …">${esc(m.text || "")}</textarea>
      <p class="hint">Sichtbar für alle angemeldeten Nutzer, mit eurem Login-Namen und euren Fahrzeugmaßen.</p>
      <p class="reverr" hidden></p>
      <div class="revactions"><button type="button" data-rsave>Speichern</button>${j.mine ? `<button type="button" class="ghost" data-rdel="" data-key="${esc(j.mine.place)}">Löschen</button>` : ""}<button type="button" class="ghost" data-rclose>Schließen</button></div>
    </form>`;
  revDlg.dataset.stars = m.stars || 0;
  revDlg.dataset.fit = m.fit || "";
}
function revCsrf() { return (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || ""; }
function onRevClick(e) {
  const t = e.target, p = revPoi;
  if (t.closest("[data-rclose]")) { revDlg.close(); return; }
  const st = t.closest("[data-star]");
  if (st) { revDlg.dataset.stars = st.dataset.star; revDlg.querySelectorAll("[data-star]").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.star <= +st.dataset.star))); return; }
  const fi = t.closest("[data-fit]");
  if (fi) { const on = revDlg.dataset.fit !== fi.dataset.fit; revDlg.dataset.fit = on ? fi.dataset.fit : ""; revDlg.querySelectorAll("[data-fit]").forEach((b) => b.setAttribute("aria-pressed", String(on && b === fi))); return; }
  const del = t.closest("[data-rdel]");
  if (del) {
    const q = new URLSearchParams({ key: del.dataset.key, ...(del.dataset.rdel ? { user: del.dataset.rdel } : {}) });
    fetch(`/api/reviews?${q}`, { method: "DELETE", headers: { "X-RA-CSRF": revCsrf() } }).then(() => { ensureReviews(true); openReviews(p); });
    return;
  }
  if (t.closest("[data-rsave]")) {
    const f = revDlg.querySelector(".revform"), err = revDlg.querySelector(".reverr");
    if (!+revDlg.dataset.stars) { err.textContent = "Bitte Sterne wählen."; err.hidden = false; return; }
    const body = { key: revKey(p), name: campName(p), lat: p.lat, lon: p.lon, stars: +revDlg.dataset.stars,
      night: f.night.value, price: f.price.value, fit: revDlg.dataset.fit || null, text: f.text.value,
      veh: { length: veh.length, height: veh.height, weight: veh.weight } };
    fetch("/api/reviews", { method: "POST", headers: { "Content-Type": "application/json", "X-RA-CSRF": revCsrf() }, body: JSON.stringify(body) })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Fehler ${r.status}`); })
      .then(() => { ensureReviews(true); openReviews(p); })
      .catch((x) => { err.textContent = x.message; err.hidden = false; });
  }
}
document.addEventListener("click", (e) => {
  const b = e.target.closest(".revbtn");
  if (!b) return;
  const base = combined || route;
  const p = (base && base.pois.find((x) => x.group === "camp" && revKey(x) === b.dataset.rev)) || campExtraPois.get(b.dataset.rev);
  if (p) { e.stopPropagation(); openReviews(p); }
}, true);

const FR_KIND = { aire: "Aire de camping-car", camping: "Camping", farm: "Camping à la ferme", nature: "Naturplatz" };
// gleiche Website wie aus OSM nicht doppelt zeigen
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
function frBox(x, osmWeb = "") {
  const web = x.w && hostOf(x.w) !== hostOf(osmWeb) ? x.w : "";
  return `<div class="frbox">🇫🇷 <b>Tourismusamt</b>${x.s ? ` · ${"★".repeat(x.s)} offiziell klassifiziert` : ""}`
    + `${FR_KIND[x.k] ? ` · ${FR_KIND[x.k]}` : ""}${x.u ? ` · Stand ${esc(x.u.split("-").reverse().join("."))}` : ""}`
    + `${x.a ? `<br><small>${esc(x.a)}</small>` : ""}${x.d ? `<br><i lang="fr">${esc(x.d)}</i>` : ""}`
    + `${web || x.r ? "<br>" : ""}${web ? `<a href="${esc(web)}" target="_blank" rel="noopener">Website ↗</a>` : ""}`
    + `${x.r ? `${web ? " · " : ""}<a href="${esc(x.r)}" target="_blank" rel="noopener">Reservieren ↗</a>` : ""}</div>`;
}

// Popup als Detail-Karte (detailcard.js) – Kopf, Knoepfe, Fotos, Zeiten, Leistungen; die
// Bloecke (Laenge, Bewertungen, Tourismusamt) stehen darin als eigene Abschnitte. planner: ohne Nacht-/Tour-Knopf.
const CAMP_ICON = (p) => (p.label === "Entsorgung" ? "🚽" : isStation(p) ? "🚰" : (p.flags || {}).kind === "camp_site" ? "🏕" : "🅿");
function campCard(p, planner = false) {
  const f = p.flags || {}, extra = [], top = [], station = isStation(p);
  if (station && WATER_TEXT[p.water_kind || "unknown"] && (p.label !== "Entsorgung" || p.water_kind === "guests")) {
    extra.push({ html: `<p class="dc-note"><i>${WATER_TEXT[p.water_kind || "unknown"]}</i></p>` });
  }
  if (!station) {
    const fit = CampFilter.fitsRig(f, veh);
    top.push({ html: `<p class="dc-fit">${campLight(p)}max. Länge ${f.maxlength ? fmt(f.maxlength, 1) + " m" : "?"} · max. Gewicht ${f.maxweight ? fmt(f.maxweight, 1) + " t" : "?"}`
      + `${f.maxheight ? ` · max. Höhe ${fmt(f.maxheight, 1)} m` : ""}`
      + `${f.capacity ? ` · ${esc(f.capacity)} Plätze` : ""}${fit === "no" ? (f.motorhome === false ? " – <b>keine Wohnmobile</b>" : " – <b>zu groß</b>") : ""}</p>` });
    const lb = laengeBox(p);
    if (lb) top.push({ html: lb });
    const more = [f.surface && `Untergrund: ${esc(SURFACE[f.surface] || f.surface)}`, RESERVATION[f.reservation],
      (f.fee === true || f.charge) && `Gebühr${f.charge ? ": " + esc(f.charge) : " ja"}`].filter(Boolean);
    if (more.length) extra.push({ html: `<p class="dc-note">${more.join(" · ")}</p>` });
    extra.push({ html: revLine(p) });
  }
  if (p.fr) extra.push({ html: frBox(p.fr, p.osm && p.osm.startsWith("dt:") ? "" : f.website) });
  if (p.osm && p.osm.startsWith("dt:")) extra.push({ html: "<small>Nur beim Tourismusamt eingetragen, nicht in OpenStreetMap – Größe/Ausstattung unbekannt.</small>" });
  if (f.check_date || f.operator) {
    extra.push({ html: `<small class="dc-note">${[f.operator && `Betreiber: ${esc(f.operator)}`, f.check_date && `zuletzt geprüft (OSM): ${esc(f.check_date)}`].filter(Boolean).join(" · ")}</small>` });
  }
  const photos = [];
  const lbx = p.laenge && p.laenge.luft;
  if (lbx && lbx.img) photos.push({ src: `/api/camps/laenge/bild?key=${encodeURIComponent(LAENGE_KEY(p))}`, alt: "gemessenes Luftbild",
    href: `/api/camps/laenge/bild?key=${encodeURIComponent(LAENGE_KEY(p))}`, credit: "Luftbild-Messung" });
  const cm = DetailCard.commonsPhoto(f.commons);
  if (cm) photos.push(cm);
  const more = [];
  if (!station) more.push({ href: p4nHref(p.lat, p.lon), text: "park4night ↗" });
  if (f.image && /^https:\/\//.test(f.image)) more.push({ href: f.image, text: "Foto (aus OSM) ↗" });
  const actions = [];
  if (!station && !planner) actions.push(`<button type="button" class="campnight dc-btn" data-night="${p.lat},${p.lon}" data-name="${esc(campName(p))}">🌙 Als Übernachtung</button>`);
  if (!planner) actions.push(tourChip(p));   // auch als normaler Zwischenpunkt
  const chips = [campKind(p), f.stars && ("★".repeat(Math.min(5, parseInt(f.stars, 10) || 0)) || f.stars) + " (offiziell)"].filter(Boolean);
  return DetailCard.card(p, {
    icon: CAMP_ICON(p), title: campName(p), chips, group: "camp", rating: p.rev ? { avg: p.rev.avg, n: p.rev.n } : null,
    meta: `bei km ${fmt(p.km, 1)} · ${fmt(p.off)} m abseits${p.near_stage ? ` · nahe Etappenziel ${p.near_stage}` : ""}`,
    phone: f.phone, website: p.osm && p.osm.startsWith("dt:") ? "" : f.website, email: f.email,
    hours: f.hours, seasonal: f.seasonal, desc: f.description, photos, mly: !station, actions, more, top, extra,
  });
}
function campPopup(p) { return campCard(p).outerHTML; }

function drawCampLayers(r) {
  [layers.camp, layers.dump, layers.water].forEach((l) => l.clearLayers());
  campMarkers.clear();
  for (const p of campsShown(r)) {
    const f = p.flags || {}, pop = campPopup(p), ms = [];
    const add = (k, size) => ms.push(L.marker([p.lat, p.lon], { icon: pin(k, size) }).bindPopup(pop).addTo(layers[k]));
    // reine Ver-/Entsorgungsstellen nur in ihren eigenen Ebenen, Plaetze zusaetzlich dort, wenn sie es anbieten
    if (!isStation(p)) add("camp", 13);
    if (f.dump || p.label === "Entsorgung") add("dump", 12);
    if (f.water || (isStation(p) && p.label !== "Entsorgung")) add("water", 12);
    if (p.osm) campMarkers.set(p.osm, ms);
  }
}

function renderCampFilters() {
  const box = $("camp-filters");
  box.hidden = tab !== "camp";
  if (box.hidden) return;
  const chip = ([k, t]) => `<button type="button" class="chipf" data-cf="${k}" aria-pressed="${!!campF[k]}">${t}</button>`;
  box.innerHTML = CAMP_GROUPS.map(([g, cs]) => `<div class="cfg"><span>${g}</span>${cs.map(chip).join("")}${
    g === "Ver-/Entsorgung" ? `<select data-cs="waterSel" aria-label="Welches Wasser zählt"><option value="public">Wasser/Entsorgung: nur öffentlich</option><option value="guests">auch für Gäste</option><option value="tap">auch Trinkwasserhähne</option></select>`
      : g === "Größe & Gespann" ? `<select data-cs="minCap" aria-label="Mindestens Plätze"><option value="0">Plätze: egal</option>${[5, 10, 20, 50].map((n) => `<option value="${n}">≥ ${n} Plätze</option>`).join("")}</select>`
        : g === "Kosten & Zeiten" && trip ? chip(["nearStage", "🌙 nur nahe Etappenziel"]) : ""}</div>`).join("");
  box.querySelector('[data-cs="waterSel"]').value = campF.waterSel || "public";
  box.querySelector('[data-cs="minCap"]').value = String(campF.minCap || 0);
}

/** „➕ In die Tour“ (shops.js, nur RigAnalyzer mit Planer-Route) – sonst "" */
const tourChip = (p) => (typeof ShopList !== "undefined" && ShopList.tourChipHtml ? ShopList.tourChipHtml({ ...p, name: campName(p) }, "camp") : "");

function renderCampList(r) {
  const all = (r.pois || []).filter((p) => p.group === "camp");
  const shown = campsShown(r).sort((a, b) => a.km - b.km);
  const head = `<li class="empty">${shown.length} von ${all.length} Orten · <span class="sev green"></span>passt <span class="sev lgreen"></span>wahrscheinlich <span class="sev amber"></span>unbekannt <span class="sev orange"></span>wohl zu kurz <span class="sev red"></span>zu groß</li>`;
  $("list").innerHTML = shown.length ? head + shown.map((p) => {
    const f = p.flags || {};
    const water = isStation(p) && p.water_kind && p.water_kind !== "public" && (p.label !== "Entsorgung" || p.water_kind === "guests")
      ? ` · ${WATER_TEXT[p.water_kind] || ""}` : "";
    const rv = p.rev ? ` <span class="revtag" title="eigene Bewertungen">⭐ ${fmt(p.rev.avg, 1)} (${p.rev.n})</span>` : "";
    const fr = p.fr ? ` <span class="frtag" title="Angaben des franz. Tourismusamts">🇫🇷${p.fr.s ? " " + "★".repeat(p.fr.s) : ""}</span>` : "";
    const chip = tourChip(p);
    return `<li data-ll="${p.lat},${p.lon}" data-osm="${esc(p.osm || "")}"${chip ? ' class="shoprow"' : ""}><span class="km">km ${fmt(p.km, 1)}</span><span>${campLight(p)}${esc(campName(p))}${rv}${fr} <span class="csym">${campSymbols(f)}</span>`
      + `<small>${esc(campKind(p))} · ${fmt(p.off)} m abseits${p.near_stage ? ` · nahe Etappenziel ${p.near_stage}` : ""}${esc(water)}</small></span>${chip}</li>`;
  }).join("") : `<li class="empty">${all.length ? "Kein Ort passt zu allen Filtern – ein paar Filter abwählen." : "Nichts gefunden."}</li>`;
}

function campsChanged() {
  saveCampF();
  if (!route) return;
  renderCampFilters(); renderCampList(route); drawCampLayers(route); syncToggles();
}
$("camp-filters").addEventListener("click", (e) => {
  const b = e.target.closest("[data-cf]");
  if (!b) return;
  campF[b.dataset.cf] = !campF[b.dataset.cf];
  campsChanged();
});
$("camp-filters").addEventListener("change", (e) => {
  const s = e.target.closest("[data-cs]");
  if (!s) return;
  campF[s.dataset.cs] = s.dataset.cs === "minCap" ? Number(s.value) || 0 : s.value;
  campsChanged();
});
// Einfahrt-Foto (Mapillary): erst beim Oeffnen eines Platz-Popups (bzw. der Planer-Details) holen
const mlyCache = new Map();
map.on("popupopen", (e) => {
  const root = e.popup.getElement();
  const el = root && root.querySelector(".mlyph");
  if (el) loadMly(el, () => e.popup.update());
  // Detail-Karte: auf-/zugeklappte Abschnitte aendern die Hoehe – Popup neu ausrichten
  if (root && !root.dataset.dcToggle) { root.dataset.dcToggle = "1"; root.addEventListener("toggle", () => e.popup.update(), true); }
});
function loadMly(el, changed = () => {}) {
  if (!el || el.dataset.done) return;
  el.dataset.done = "1";
  const ll = el.dataset.mly, show = (j) => {
    const ph = j && j.photo;
    if (!ph) { el.remove(); changed(); return; }
    el.innerHTML = `<a href="${esc(ph.link)}" target="_blank" rel="noopener" title="Bei Mapillary ansehen (360°/weitere Bilder)">`
      + `<img src="${esc(ph.thumb)}" alt="Straßenfoto bei der Einfahrt" loading="lazy"></a>`
      + `<small>📷 ${ph.dist} m entfernt${ph.at ? ` · ${new Date(ph.at * 1000).toLocaleDateString("de-DE", { month: "2-digit", year: "numeric" })}` : ""}`
      + `${ph.looks ? "" : " · schaut evtl. woanders hin"} · ${esc(j.credit || "Mapillary")}</small>`;
    el.querySelector("img").addEventListener("load", changed);
  };
  if (mlyCache.has(ll)) { show(mlyCache.get(ll)); return; }
  const [lat, lon] = ll.split(",");
  fetch(`/api/camps/photo?lat=${lat}&lon=${lon}`).then((r) => (r.ok ? r.json() : null))
    .then((j) => { mlyCache.set(ll, j); show(j); }).catch(() => el.remove());
}

/* Plaetze ausserhalb der analysierten Route (Planer-Vorschlaege): Details + Bewertungen wie im Popup */
const campExtraPois = new Map();   // revKey -> Platz (fuer „bewerten“)
// biome-ignore lint/correctness/noUnusedVariables: globale Funktion, wird von planner.js/rig.js (andere Skripte) genutzt
function campFromSuggestion(o) {
  const p = { group: "camp", label: o.kind === "caravan_site" ? "Wohnmobil-Stellplatz" : "Campingplatz", name: o.name,
    lat: o.lat, lon: o.lon, km: o.at_km || 0, off: Math.round((o.off_km || 0) * 1000), osm: o.osm,
    flags: { ...(o.flags || {}), kind: o.kind } };
  campExtraPois.set(revKey(p), p);
  return p;
}
/** Tourismusamt + eigene Bewertungen fuer einen einzelnen Platz nachladen (Planer-Details). */
// biome-ignore lint/correctness/noUnusedVariables: globale Funktion, wird von planner.js/rig.js (andere Skripte) genutzt
function enrichCamp(p) {
  const fr = inFR(p.lat, p.lon) ? fetch("/api/camps/fr", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pois: [[p.osm || revKey(p), p.lat, p.lon, p.name || ""]], line: [] }) }).then((r) => (r.ok ? r.json() : null))
    .then((j) => { const m = j && j.match && j.match[p.osm || revKey(p)]; if (m) p.fr = m; }).catch(() => {}) : Promise.resolve();
  const rv = fetch("/api/reviews/lookup", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ places: [[revKey(p), p.lat, p.lon]] }) }).then((r) => (r.ok ? r.json() : null))
    .then((j) => { p.rev = (j && j.places[revKey(p)]) || null; }).catch(() => {});
  return Promise.all([fr, rv]).then(() => p);
}
/** Details-HTML ohne Karten-Knoepfe „Als Übernachtung“/„In die Tour“ (im Planer gibt es eigene Knoepfe). */
// biome-ignore lint/correctness/noUnusedVariables: globale Funktion, wird von planner.js/rig.js (andere Skripte) genutzt
function campDetails(p) {
  return campCard(p, true).outerHTML;
}

// „Als Uebernachtung uebernehmen“ im Popup -> Nacht-Punkt im Planer
map.getContainer().addEventListener("click", (e) => {
  const b = e.target.closest(".campnight");
  if (!b) return;
  const [lat, lon] = b.dataset.night.split(",").map(Number);
  if (window.planner && window.planner.addNight({ lat, lon, name: b.dataset.name })) {
    b.disabled = true; b.textContent = "✓ als Nacht-Punkt übernommen – wird neu geplant …";
  } else {
    b.outerHTML = `<small>Geht nur bei Routen aus dem Routenplaner – dort den Platz als Punkt setzen und „Nacht“ anhaken.</small>`;
  }
});
// Klick in der Liste: rig.js zoomt hin, hier zusaetzlich das Popup mit den Details oeffnen
$("list").addEventListener("click", (e) => {
  const li = e.target.closest("[data-osm]");
  const ms = (li && campMarkers.get(li.dataset.osm)) || [];
  if (!ms.length) return;
  // ausgeblendete Ebene dafuer einschalten, sonst gaebe es kein Popup
  const k = ["camp", "dump", "water"].find((n) => layers[n].hasLayer(ms[0]));
  const cb = document.querySelector(`#toggles input[data-m="${k}"]`);
  if (cb && !cb.checked) { cb.checked = true; syncToggles(); }
  setTimeout(() => ms[0].openPopup(), 50);
});
