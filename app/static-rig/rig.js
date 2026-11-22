/* RigAnalyzer – Oberflaeche. Die Engine (Server) liefert Rohdaten; hier wird alles
   gegen die Fahrzeugdaten bewertet: Passt es durch? Wo tanken? Wie steil? */
"use strict";

const $ = (id) => document.getElementById(id);

/* Vorlieben am Login: Fahrzeugprofile, Zoom, Reiter + Kartenebenen – Webseite und RigNavi teilen sie.
   Je Bereich ein Zeitstempel; der neuere Stand gewinnt. Ohne Login/Netz bleibt alles wie bisher im Browser. */
const Prefs = {
  data: {}, synced: false, t: {},
  WHEEL: { normal: 22, schnell: 12, sehr_schnell: 6 },   // Pixel Mausrad je Zoomstufe (kleiner = schneller)
  zoom() { return { wheel: "schnell", jump: 16, ...(this.data.zoom || {}) }; },
  put(part, obj) {
    this.data[part] = { ...obj, at: Date.now() };
    clearTimeout(this.t[part]);
    this.t[part] = setTimeout(() => {
      const csrf = (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
      fetch("/api/me/prefs", { method: "PUT", headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf },
        body: JSON.stringify({ [part]: this.data[part] }) }).catch(() => {});
    }, 700);
  },
};
window.Prefs = Prefs;

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
const hm = (h) => `${Math.floor(h)}:${String(Math.round((h % 1) * 60)).padStart(2, "0")} h`;
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* ---------------------------------------------------------------- Fahrzeug + Profile */
// Standardprofil zum Starten; eigene Werte/Profile landen im Cookie (1 Jahr) und
// zusaetzlich im localStorage (falls Cookies blockiert sind).
// Standard = Hobby-Wohnmobil laut Datenblatt + CoC, ohne Anhaenger: 7426x2330x2872 mm,
// zGG 3500 kg, Achslast max. 2000 kg (hinten), Wendekreis 14,43 m, 90 l Diesel, 20 l AdBlue; Verbrauch 11,5 l/100 km
const DEFAULTS = {
  height: 2.87, width: 2.33, length: 7.43, weight: 3.5, zgg: 3.5, axle: 2.0, trailer: false, tempo100: false, vmax: 110, turn: 14.43,
  tank: 90, cons: 11.5, reserve: 15, start: 100, price: 2.14, truck: false, nomw: true,
  adtank: 20, adcons: 0.33, adstart: 100,
  euro: 6, fuel: "diesel", critair_vignette: false, lez_registered: false,   // Umweltzonen, Euro 0 = unbekannt, Standard Euro 6
};
// fruehere Standardwerte – unveraenderte Profile ziehen auf die neuen um
const OLD_DEFAULTS = [{
  height: 3.2, width: 2.35, length: 12, weight: 5.0, zgg: 3.5, axle: 2.5, trailer: true, tempo100: false, vmax: 80,
  tank: 90, cons: 12, reserve: 15, start: 100, price: 1.75, truck: false,
  adtank: 10, adcons: 0.33, adstart: 100,
}, { ...DEFAULTS, height: 2.9, cons: 10, price: 1.75 }, { ...DEFAULTS, height: 2.9, cons: 10 }];
const FIELDS = Object.keys(DEFAULTS);
const COOKIE = "rig_profiles";
const MAX_PROFILES = 8;

function readCookie(name) {
  const m = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return m ? decodeURIComponent(m.slice(name.length + 1)) : null;
}
function loadStore() {
  let st = null;
  try { st = JSON.parse(readCookie(COOKIE) || "null"); } catch { st = null; }
  if (!st) { try { st = JSON.parse(localStorage.getItem("rig.profiles") || "null"); } catch { st = null; } }
  if (!st) {   // Uebernahme aus der ersten Version (ein einzelnes Fahrzeug im localStorage)
    let old = null;
    try { old = JSON.parse(localStorage.getItem("rig.vehicle") || "null"); } catch { old = null; }
    st = { active: "Standard", profiles: { Standard: { ...DEFAULTS, ...(old || {}) } }, fresh: !old };
  }
  if (!st.profiles || !Object.keys(st.profiles).length) st.profiles = { Standard: { ...DEFAULTS } };
  for (const k of Object.keys(st.profiles)) {
    const p = st.profiles[k];
    const untouched = OLD_DEFAULTS.some((o) => Object.keys(o).every((f) => p[f] === undefined || p[f] === o[f]));
    st.profiles[k] = untouched ? { ...DEFAULTS } : { ...DEFAULTS, turn: 0, ...p };   // eigene Profile: Wendekreis erst mal unbekannt
  }
  if (!st.profiles[st.active]) st.active = Object.keys(st.profiles)[0];
  return st;
}
const store = loadStore();
let veh = store.profiles[store.active];

// at: Zeitstempel des Stands. Ein frischer Browser speichert mit 0 (sonst waere der Standard „neuer“ als das
// Profil am Login und wuerde es nie uebernehmen), ein vom Server uebernommener Stand behaelt den Zeitstempel des Servers.
function saveStore(flash = true, sync = true, at = Date.now()) {
  store.at = at;
  const data = JSON.stringify({ active: store.active, profiles: store.profiles, at: store.at });
  // auch am Login ablegen (andere Browser, RigNavi) – erst nach dem ersten Abgleich, sonst ueberschreibt ein
  // frischer Browser mit Standardwerten die gespeicherten Profile
  if (sync && Prefs.synced) Prefs.put("profiles", { active: store.active, profiles: store.profiles });
  const secure = location.protocol === "https:" ? "; Secure" : "";
  // biome-ignore lint/suspicious/noDocumentCookie: Cookie Store API fehlt in aelteren Safari-Versionen; der Cookie ist ein Komfort-Spiegel
  document.cookie = `${COOKIE}=${encodeURIComponent(data)}; Max-Age=31536000; Path=/; SameSite=Lax${secure}`;
  try { localStorage.setItem("rig.profiles", data); } catch { /* privat */ }
  if (flash) {
    const el = $("v-saved");
    el.textContent = `✓ gespeichert (${store.active})`;
    clearTimeout(saveStore.t); saveStore.t = setTimeout(() => { el.textContent = ""; }, 1800);
  }
}

const show = (v) => String(v).replace(".", ",");
function fillInputs() {
  for (const k of FIELDS) {
    const el = $(`v-${k}`);
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!veh[k];
    else if (el.tagName === "SELECT") el.value = String(veh[k]);
    else { el.value = show(veh[k]); el.classList.remove("bad"); }
  }
  const sel = $("v-profile");
  sel.innerHTML = Object.keys(store.profiles).map((n) => `<option ${n === store.active ? "selected" : ""}>${esc(n)}</option>`).join("");
  $("v-del").disabled = Object.keys(store.profiles).length < 2;
  vehSummary();
}

function bindVehicle() {
  for (const k of FIELDS) {
    const el = $(`v-${k}`);
    if (!el) continue;
    const handler = () => {
      let v;
      if (el.type === "checkbox") v = el.checked;
      else if (el.tagName === "SELECT") v = k === "euro" ? parseInt(el.value, 10) || 0 : el.value;
      else {
        v = parseFloat(String(el.value).trim().replace(",", "."));
        const lo = parseFloat(el.dataset.min), hi = parseFloat(el.dataset.max);
        const ok = Number.isFinite(v) && !(v < lo) && !(v > hi);
        el.classList.toggle("bad", !ok);
        el.title = ok ? "" : `Bitte eine Zahl zwischen ${show(lo)} und ${show(hi)}`;
        if (!ok) {
          const label = el.closest("label").childNodes[0].textContent.trim();
          $("v-saved").textContent = `⚠ ${label}: bitte eine Zahl zwischen ${show(lo)} und ${show(hi)} – nicht gespeichert`;
          return;
        }
      }
      veh[k] = v;
      saveStore();
      vehSummary();
      if (LEZ_KEYS[k]) pushLezSettings();
      if (route) renderAll();
    };
    const pick = el.type === "checkbox" || el.tagName === "SELECT";
    el.addEventListener(pick ? "change" : "input", handler);
    if (!pick) el.addEventListener("blur", () => { if (!el.classList.contains("bad")) el.value = show(veh[k]); });
  }
  $("v-profile").addEventListener("change", (e) => {
    store.active = e.target.value; veh = store.profiles[store.active];
    saveStore(); fillInputs(); pushLezSettings(); if (route) renderAll();
  });
  $("v-save").addEventListener("click", () => {
    if (Object.keys(store.profiles).length >= MAX_PROFILES) { alert(`Höchstens ${MAX_PROFILES} Profile.`); return; }
    const name = (prompt("Name für das neue Profil (z. B. „Womo + Hänger“):", "") || "").trim().slice(0, 30);
    if (!name) return;
    if (store.profiles[name] && !confirm(`„${name}“ gibt es schon – überschreiben?`)) return;
    store.profiles[name] = { ...veh };
    store.active = name; veh = store.profiles[name];
    saveStore(); fillInputs();
  });
  $("v-del").addEventListener("click", () => {
    if (Object.keys(store.profiles).length < 2) return;
    if (!confirm(`Profil „${store.active}“ löschen?`)) return;
    delete store.profiles[store.active];
    store.active = Object.keys(store.profiles)[0]; veh = store.profiles[store.active];
    saveStore(); fillInputs(); pushLezSettings(); if (route) renderAll();
  });
  $("v-reset").addEventListener("click", () => {
    store.profiles[store.active] = { ...DEFAULTS }; veh = store.profiles[store.active];
    saveStore(); fillInputs(); pushLezSettings(); if (route) renderAll();
  });
  fillInputs();
  // Erster Besuch: Karte offen lassen und auf eigene Werte hinweisen
  if (!store.fresh) $("v-intro").textContent = "Eure Werte werden an eurem Login gespeichert – in jedem Browser gleich. RigNavi übernimmt sie mit jeder gespeicherten Tour. Komma oder Punkt, beides geht.";
  else { $("vehicle-card").open = true; saveStore(false, true, 0); }
}
function vehSummary() {
  $("veh-sum").textContent = `${store.active}: ${show(veh.height)} m hoch · ${show(veh.width)} m breit · ${show(veh.length)} m lang · ${show(veh.weight)} t${veh.trailer ? " · Gespann" : ""}`;
  if ($("veh-pill")) $("veh-pill").textContent = `🚐 ${show(veh.height)} m · ${show(veh.length)} m · ${show(veh.weight)} t${veh.trailer ? " · mit Anhänger" : ""}`;
  // Tempo-100-Zulassung gibt es nur fuers Gespann – ohne Anhaenger ausblenden
  if ($("v-tempo100-row")) $("v-tempo100-row").style.display = veh.trailer ? "" : "none";
  // lang wie ein Gespann, aber ohne Anhaenger-Haken -> nachfragen (sonst fehlen Anhaenger-Sperren, -Tempo, -Maut)
  const th = $("v-trailer-hint");
  if (th) {
    th.hidden = !!veh.trailer || !(veh.length > 9);
    th.textContent = `${show(veh.length)} m lang, aber „mit Anhänger“ ist nicht angehakt. Fahrt ihr mit Hänger? Dann anhaken – sonst prüft die Seite keine Anhänger-Verbote, kein Gespann-Tempo und keine Gespann-Maut.`;
  }
  if ($("v-lez-derive")) $("v-lez-derive").textContent = lezDeriveText(veh);
}

/* ---------------------------------------------------------------- Umweltzonen */
// Profilwerte -> Einstellungen am Login (R2-Namen); Cookie bleibt die Quelle fuer diese Seite, Fehler still
const LEZ_KEYS = { euro: "lez_euro", fuel: "lez_fuel", critair_vignette: "critair_vignette", lez_registered: "lez_registered" };
let lezPushT = 0;
function pushLezSettings() {
  clearTimeout(lezPushT);
  lezPushT = setTimeout(() => {
    const csrf = (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
    const body = {};
    for (const [k, n] of Object.entries(LEZ_KEYS)) body[n] = k === "euro" ? Number(veh.euro) || 0 : k === "fuel" ? String(veh.fuel || "diesel") : !!veh[k];
    fetch("/api/me/settings", { method: "POST", headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf }, body: JSON.stringify(body) })
      .catch(() => { /* Zusatz */ });
  }, 600);
}
const STICKER_TXT = { green: "grüne Plakette", yellow: "gelbe Plakette", red: "rote Plakette" };
function lezDeriveText(v) {
  const fuel = v.fuel || "diesel";
  if (!(Number(v.euro) >= 1) && fuel !== "electric" && fuel !== "gas") return "→ Euro-Norm eintragen, dann zeigt die Seite Plakette, Crit'Air und Etikett";
  const d = LezUI.derive(v);
  return "→ " + [d.sticker ? STICKER_TXT[d.sticker] : "keine Plakette", d.critair != null ? `Crit'Air ${d.critair}` : "keine Crit'Air-Klasse",
    d.etiqueta ? `Etikett ${d.etiqueta}` : "kein Etikett"].join(" · ");
}
// Regel in Worten (ohne Urteil) – fuer die Hinweis-Zeile
function lezRuleText(rule) {
  const r = rule || {};
  switch (r.kind) {
    case "sticker": return `${STICKER_TXT[r.need || "green"]} nötig`;
    case "critair": {
      const max = (Number(veh.zgg) || 0) <= 3.5 ? r.vp : r.pl;
      const h = r.hours && String(r.hours) !== "24/7" ? ` (${r.hours})` : "";
      return max == null ? "nur für andere Fahrzeugklassen" : `Crit'Air ${max} oder besser${h}`;
    }
    case "etiqueta": return `Etikett ${r.need} oder besser`;
    case "euro": return [r.diesel != null ? `Diesel ab Euro ${r.diesel}` : "", r.petrol != null ? `Benzin ab Euro ${r.petrol}` : "",
      r.register ? "Registrierung" : ""].filter(Boolean).join(", ") || "Euro-Norm";
    default: return "";
  }
}
const LEZ_SRC = { bnzfe: "BNZFE (transport.data.gouv.fr)", osm: "OpenStreetMap" };
let lezMeta = null;   // Stand je Quelle aus /api/info
function lezTip(z) {
  const st = lezMeta && lezMeta[z.source];
  return `Quelle: ${LEZ_SRC[z.source] || z.source || "?"}${st ? ` · Stand ${deDate(String(st).slice(0, 10))}` : ""}`
    + `${z.since ? ` · gilt ab ${deDate(z.since)}` : ""}${z.until ? ` bis ${deDate(z.until)}` : ""}`;
}
// Tag der Durchfahrt einer Etappe aus dem Fahrplan (Startdatum + Naechte); ohne Startdatum null
function stageWhen(n) {
  if (!trip) return null;
  const cfg = tripCfg();
  if (!cfg.date) return null;
  const last = trip.stages.length - 1;
  let day = 0;
  for (let i = 0; i < trip.stages.length; i++) {
    const s = trip.stages[i];
    if (s.stage.n === n) {
      const d = new Date(cfg.date + "T00:00");
      d.setDate(d.getDate() + day);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    day += Math.max(cfg.nights[i] ?? s.stage.nights ?? (i === last ? 0 : 1), 0);
  }
  return null;
}
/* Zonen einer Route mit Urteil: je Etappe die zur Durchfahrt gueltige Stufe (pickStage), je Name eine.
   null = kein Zonenstand (Server: lez null bzw. alter Cache ohne Feld). */
function lezZones(r) {
  const raw = r && r.rig ? r.rig.lez : undefined;
  if (raw == null) return null;
  const own = r.stage ? r.stage.n : null, by = new Map();
  for (const z of raw) {
    const n = z.stage ?? own;
    if (!by.has(n)) by.set(n, []);
    by.get(n).push(z);
  }
  const out = [];
  for (const [n, zs] of by) {
    const when = n != null ? stageWhen(n) : null;
    for (const z of LezUI.pickStage(zs, when)) {
      const v = LezUI.verdict(z, veh, when);
      out.push({ ...z, stage: n ?? undefined, verdict: v });
    }
  }
  return out.sort((a, b) => a.km0 - b.km0);
}
const lezName = (z) => String(z.name || z.id).replace(/^Umweltzone\s+/i, "");   // OSM-Namen tragen es oft schon
const LEZ_SEV = { no: "red", check: "amber", ok: "green" };
const LEZ_COLOR = { no: "#e03131", check: "#f08c00", ok: "#2f9e44" };
/** IDs der Zonen mit Klassen-„no“ (ohne die, wo nur Vignette/Registrierung fehlt) – fuer avoid_zones im Planer */
function lezAvoidIds() {
  const zs = lezZones(combined || route) || [];
  return [...new Set(zs.filter((z) => z.verdict.v === "no" && !z.verdict.need).map((z) => z.id))];
}
window.lezAvoidIds = lezAvoidIds;

/* ---------------------------------------------------------------- Bewertung */
const SEV_ORDER = { red: 0, amber: 1, info: 2, green: 3 };   // green: Umweltzone, in die ihr duerft (dezent)

function evaluate(r) {
  const out = [];
  const rig = r.rig || {};
  const add = (sev, it, title, detail) => out.push({ sev, km0: it.km0 ?? it.km, km1: it.km1 ?? it.km, lat: it.lat, lon: it.lon, title,
    detail: detail + (it.stage ? ` · Etappe ${it.stage}` : ""), stage: it.stage, tip: it.tip, lez: it.lez,
    kind: it.kind || (it.steep ? "steep" : "hairpin"), dim: ["height", "width", "weight", "length", "axle"].includes(it.kind) && it.value != null });
  for (const it of rig.restrictions || []) {
    const v = it.value, where = it.name ? ` · ${it.name}` : "";
    const m = (x, u) => `${fmt(x, 2)} ${u}`;
    switch (it.kind) {
      case "height":
        if (v == null) add("amber", it, it.label, `Höhe nicht angegeben – vor Ort prüfen${where}`);
        else if (v < veh.height) add("red", it, `Zu niedrig: ${m(v, "m")}`, `${it.label}, euer Fahrzeug ${m(veh.height, "m")}${where}`);
        else if (v < veh.height + 0.2) add("amber", it, `Knapp: ${m(v, "m")}`, `${it.label}, nur ${fmt((v - veh.height) * 100)} cm Luft${where}`);
        break;
      case "tunnel": add("info", it, it.label, `Durchfahrtshöhe nicht in der Karte – Beschilderung beachten${where}`); break;
      case "width":
        if (v < veh.width) add("red", it, `Zu schmal: ${m(v, "m")}`, `${it.label}, euer Fahrzeug ${m(veh.width, "m")}${where}`);
        else if (v < veh.width + 0.3) add("amber", it, `Knapp: ${m(v, "m")}`, `${it.label}, ${fmt((v - veh.width) * 100)} cm Luft (ohne Spiegel)${where}`);
        break;
      case "weight":
        if (v < veh.weight) add("red", it, `Zu schwer: max. ${m(v, "t")}`, `${it.label}, ihr wiegt ${m(veh.weight, "t")}${where}`);
        break;
      case "weight_hgv":
        if (veh.zgg > 3.5 && v < veh.weight) add("amber", it, `Lkw max. ${m(v, "t")}`, `${it.label} – gilt evtl. auch für Wohnmobile über 3,5 t${where}`);
        break;
      case "axle":
        if (v < veh.axle) add("red", it, `Achslast max. ${m(v, "t")}`, `${it.label}, eure Achslast ${m(veh.axle, "t")}${where}`);
        break;
      case "length":
        if (v < veh.length) add("red", it, `Zu lang: max. ${m(v, "m")}`, `${it.label}, ihr seid ${m(veh.length, "m")} lang${where}`);
        break;
      case "closed": add("red", it, it.label, `${it.raw}${where}`); break;
      case "destination": add("amber", it, it.label, `Durchfahrt nur mit Ziel dort${where}`); break;
      case "hgv":
        if (veh.zgg > 3.5) add(it.raw.endsWith("no") ? "red" : "amber", it, it.label, `Zeichen „Verbot über 3,5 t“ – trifft auch schwere Wohnmobile${where}`);
        break;
      case "trailer": case "caravan":
        if (veh.trailer) add("red", it, it.label, `Mit Anhänger gesperrt${where}`);
        break;
      case "motorhome": add("red", it, it.label, where.slice(3)); break;
      case "roadwidth":
        if (v == null) add("amber", it, "Schmale Straße", `Begegnungsverkehr wird eng${where}`);
        else if (v < veh.width + 0.6) add("red", it, `Sehr schmal: ${m(v, "m")}`, `Kaum breiter als euer Fahrzeug${where}`);
        else if (v < 2 * veh.width + 0.6) add("amber", it, `Schmal: ${m(v, "m")}`, `Begegnung nur mit Ausweichen${where}`);
        break;
      case "singlelane": add("amber", it, it.label, `Ausweichstellen nutzen, ggf. rückwärts${where}`); break;
      case "incline": if (v >= 12) add("amber", it, `Steigung ${fmt(v)} % laut Schild`, where.slice(3)); break;
      default: break;
    }
  }
  // Kehren: Gespann braucht Platz (grobe Faustregel ueber die Gesamtlaenge)
  // Mindestradius: halber Wendekreis (falls bekannt), beim Gespann zaehlt eher die Gesamtlaenge
  const rmin = Math.max(veh.turn ? veh.turn / 2 : 0, veh.trailer || !veh.turn ? veh.length * 0.9 : 0);
  for (const h of rig.hairpins || []) {
    if (h.r < rmin) add("red", h, `Sehr enge Kehre (Radius ≈ ${h.r} m)`, `Für ${fmt(veh.length, 1)} m${veh.turn ? ` und ${fmt(veh.turn, 1)} m Wendekreis` : ""} vermutlich nur mit Rangieren`);
    else if (h.r < rmin * 1.6 && h.cls === "kehre") add("amber", h, `Enge Kehre (Radius ≈ ${h.r} m)`, "Weit ausholen, Gegenverkehr beachten");
  }
  for (const s of rig.steep || []) {
    const lim = veh.trailer ? 10 : 12;
    if (s.max >= lim) add("amber", { ...s, steep: true }, `${s.dir === "up" ? "Steigung" : "Gefälle"} bis ${fmt(s.max)} %`,
      `${fmt(s.len / 1000, 1)} km, im Schnitt ${fmt(s.avg)} %${s.max >= lim + 4 ? (veh.trailer ? " – mit Anhänger sehr fordernd" : " – sehr steil") : ""}${s.dir === "down" ? " · Motorbremse nutzen" : ""}`);
  }
  // Umweltzonen: je Zone ein Eintrag, Urteil gegen das Fahrzeugprofil; kein Stand -> deutlich sagen
  const zs = lezZones(r);
  if (zs === null) add("info", { km: 0, kind: "lez", ...(r.segments && r.segments.length ? { lat: r.segments[0].c[0][0], lon: r.segments[0].c[0][1] } : {}) },
    "Umweltzonen gerade nicht verfügbar", "Zonendaten fehlen gerade – Umweltzonen vor der Reise selbst prüfen");
  for (const z of zs || []) {
    const ll = r.segments && r.segments.length ? llAtKm(r, z.km0) : [null, null];
    const rule = lezRuleText(z.rule);
    add(LEZ_SEV[z.verdict.v], { km0: z.km0, km1: z.km1, lat: ll[0], lon: ll[1], stage: z.stage, kind: "lez", tip: lezTip(z), lez: z },
      `Umweltzone ${lezName(z)}`, `km ${fmt(z.km0)}${Math.round(z.km1) > Math.round(z.km0) ? `–${fmt(z.km1)}` : ""}${rule ? ` · ${rule}` : ""} · ${z.verdict.text}`);
  }
  out.sort((a, b) => SEV_ORDER[a.sev] - SEV_ORDER[b.sev] || a.km0 - b.km0);
  return out;
}

/* ---------------------------------------------------------------- Fahrzeit */
function travelHours(r) {
  let h = 0;
  for (const s of r.segments) {
    const v = Math.max(5, Math.min(s.rv || 60, veh.vmax));
    h += (s.km1 - s.km0) / v;
  }
  return h;
}

/* ---------------------------------------------------------------- Tankplan */
// Uhrzeit, zu der man an einer Station einer Reise ankommt: Abfahrt der Etappe + Fahrzeit bis dorthin
// (wie im Zeitplan, Pause anteilig) – fuer die 12-Uhr-Regel in Deutschland
function stopClock(s) {
  if (!trip || !combined || s.stage == null) return null;
  const [hh, mm] = (tripCfg().time || "08:30").split(":").map(Number);
  let h = 0, all = 0;
  for (const g of combined.segments) {
    if (g.stage !== s.stage) continue;
    const v = Math.max(5, Math.min(g.rv || 60, veh.vmax)), len = g.km1 - g.km0;
    all += len / v;
    if (g.km0 < s.km) h += (Math.min(g.km1, s.km) - g.km0) / v;
  }
  const segs = combined.segments.filter((g) => g.stage === s.stage);
  const km = segs.reduce((a, g) => a + g.km1 - g.km0, 0);
  const pause = Aufenthalt.pauseHours(km, all, tripCfg().pause);   // wie schedule()
  const idx = trip.stages.findIndex((x) => x.stage.n === s.stage);
  const k0 = segs.length ? Math.min(...segs.map((g) => g.km0)) : 0;
  const before = (tripStays()[idx] || []).filter((x) => x.km + k0 <= s.km).reduce((a, x) => a + x.min, 0) / 60;
  return hh + (mm || 0) / 60 + h + (all ? pause * h / all : 0) + before;
}
// Liegt die Station in Deutschland? Echtpreis weiss es (cc), sonst grob nach Lage (Nachbarn an der Grenze ausgenommen)
function inDE(s) {
  if (s.live) return (s.live.cc || "DE") === "DE";
  const { lat, lon } = s;
  return lat > 47.55 && lat < 55.1 && lon > 5.9 && lon < 15.0
    && !(lon < 7.7 && lat < 49.2) && !(lon < 6.55 && lat < 50.2) && !(lon > 12.9 && lat < 48.75) && !(lat > 50.75 && lon < 6.0);
}
function stationsOf(r) {
  return FuelPlan.calibrate(FuelPlan.withLivePrices(r.pois.filter((p) => p.group === "fuel" && (!p.flags || (p.flags.diesel !== false && !p.flags.card_only)))
    .sort((a, b) => a.km - b.km), livePrices));
}

/* Echte Dieselpreise (DE Tankerkönig, FR/ES/IT aus Behoerdendaten): einmal je geladener Route ueber den Server */
let livePrices = {}, liveFor = null, liveInfo = null, liveBusy = false, liveChecked = 0, livePlan = null;
let livePending = [], liveRetry = 0;   // Laender, deren Preisliste auf dem Server noch laedt (ES/IT) -> spaeter nochmal fragen
// Mehrere Runden: mit echten Preisen plant der Tankplan oft andere Stopps – fuer die dann auch fragen,
// bis jeder Stopp seine Umgebung abgefragt hat (hoechstens 6 Runden je Route; schon Gefragtes nicht nochmal)
let liveAsked = new Set(), liveRounds = 0;
let liveSources = {}, liveNone = new Set();   // Quellen je Land (alle Runden); Tankstellen ohne Treffer (Liste „Preis holen“)
function liveSummary() {
  const per = {}, avg = {};
  Object.values(livePrices).forEach((x) => {
    if (x.avg) { avg[x.cc] = x.diesel; return; }   // Landesschnitt zaehlt nicht als echter Preis
    per[x.cc || "DE"] = (per[x.cc || "DE"] || 0) + 1;
  });
  const n = Object.values(per).reduce((a, b) => a + b, 0);
  liveInfo = n ? { n, per, source: Object.entries(liveSources).filter(([c]) => per[c]).map(([, t]) => t).join(" · ") } : null;
  liveAvg = avg;
}
let liveAvg = {};
function ensurePrices(plan, force = false) {
  const base = combined || route;
  livePlan = plan;
  if (!base || liveBusy) return;
  if (liveFor !== base) {
    liveFor = base; livePrices = {}; liveInfo = null; livePending = []; liveAsked = new Set(); liveRounds = 0; liveRetry = 0;
    liveSources = {}; liveNone = new Set();
  }
  // nur rund um die geplanten Stopps – nicht die ganze Route
  let cand = FuelPlan.priceCandidates(stationsOf(base), plan.diesel.stops || []);
  if (!force) cand = cand.filter((c) => !liveAsked.has(c[0]));
  if (!cand.length || (!force && liveRounds >= 6)) return;
  if (force) liveRetry = 0;
  liveRounds++;
  cand.forEach((c) => liveAsked.add(c[0]));
  liveBusy = true;
  // force = Knopf „Preise jetzt prüfen“: Server nimmt Zwischengespeichertes dann nur bis 2 min alt
  fetch("/api/fuel/prices", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stations: cand, fresh: force }) })
    .then((r) => (r.ok ? r.json() : null))
    .finally(() => { liveBusy = false; })
    .then((j) => {
      if (!j || liveFor !== base) { if (force) renderAll(); return; }
      liveChecked = j.checked || Math.floor(Date.now() / 1000);
      livePending = j.pending || [];
      if (livePending.length && liveRetry < 4) {
        liveRetry++;
        // Liste laedt noch: diese Kandidaten spaeter nochmal fragen
        setTimeout(() => { if (liveFor === base) { cand.forEach((c) => liveAsked.delete(c[0])); liveRounds--; ensurePrices(livePlan); } }, 15000);
      }
      Object.assign(livePrices, j.prices || {});
      Object.assign(liveSources, j.sources || {});
      liveSummary();
      renderAll();   // rechnet neu und fragt ggf. fuer neu geplante Stopps nach (naechste Runde)
    }).catch(() => { if (force) renderAll(); /* Zusatz – bleibt bei der Schaetzung */ });
}
const hhmmT = (t) => new Date(t * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
// Preisstand: heute nur Uhrzeit, sonst mit Datum (Italien meldet den Stand von 8 Uhr, Spanien/Frankreich teils aelter)
const whenT = (t) => { const d = new Date(t * 1000); return d.toDateString() === new Date().toDateString() ? hhmmT(t)
  : `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}. ${hhmmT(t)}`; };
const CC_DE = { DE: "Deutschland", FR: "Frankreich", ES: "Spanien", IT: "Italien", DK: "Dänemark", SE: "Schweden", NO: "Norwegen" };
// „Quelle ↗“ je Land: Preisportal der Behoerde (eine Seite je Tankstelle bieten nicht alle stabil an)
const PRICE_SITE = { DE: "https://www.clever-tanken.de/", FR: "https://www.prix-carburants.gouv.fr/",
  ES: "https://geoportalgasolineras.es/", IT: "https://carburanti.mise.gov.it/ospzSearch/home" };
const extLink = (href, text) => ` <a class="ext" href="${esc(href)}" target="_blank" rel="noopener">${text} ↗</a>`;
// Ort in einer anderen App oeffnen (statt OSM-Seite): Android geo: (Auswahl: Google Maps, OsmAnd, RigNavi …),
// iPhone/iPad/Mac Apple Karten, sonst Google Maps im Browser; dazu Koordinaten zum Kopieren
const UA = navigator.userAgent;
const IS_ANDROID = /Android/i.test(UA), IS_APPLE = /iPhone|iPad|iPod|Macintosh/i.test(UA);
function appHref(lat, lon, name) {
  const ll = `${(+lat).toFixed(6)},${(+lon).toFixed(6)}`, q = encodeURIComponent(name || "Ziel");
  if (IS_ANDROID) return `geo:${ll}?q=${ll}(${q})`;
  if (IS_APPLE) return `https://maps.apple.com/?ll=${ll}&q=${q}`;
  return `https://www.google.com/maps/search/?api=1&query=${ll}`;
}
function placeLinks(lat, lon, name) {
  const ll = `${(+lat).toFixed(5)}, ${(+lon).toFixed(5)}`;
  return ` <a class="ext" href="${esc(appHref(lat, lon, name))}"${IS_ANDROID ? "" : ' target="_blank" rel="noopener"'}>In Karten-App ↗</a>`
    + ` <button type="button" class="coord" data-copy="${ll}" title="Koordinaten kopieren">📋 ${ll}</button>`;
}
// Koordinaten kopieren – ueberall auf der Seite und in Karten-Popups
document.addEventListener("click", (e) => {
  const b = e.target.closest("button.coord");
  if (!b) return;
  e.stopPropagation();
  const done = () => { const t = b.textContent; b.textContent = "✓ kopiert"; setTimeout(() => { b.textContent = t; }, 1400); };
  (navigator.clipboard ? navigator.clipboard.writeText(b.dataset.copy) : Promise.reject()).then(done)
    .catch(() => { window.prompt("Koordinaten:", b.dataset.copy); });
}, true);

const { isMw, isDetour } = FuelPlan;
const planTank = FuelPlan.planTank;
const PRICE_BASE = 1.85;   // ohne eigenen Preis im Profil

function fuelPlan(r, startOverride = null) {
  const total = r.stats.km;
  const stations = stationsOf(r);
  const diesel = planTank({
    total, stations, cap: veh.tank, consPerKm: veh.cons / 100, reserve: veh.reserve,
    startL: startOverride ?? veh.tank * veh.start / 100, prefer: veh.truck ? (s) => s.flags && s.flags.truck : null,
    avoid: veh.nomw ? isMw : null, base: veh.price || PRICE_BASE,
    unsure: Object.keys(livePrices).length ? 0.05 : 0,
    // Reise mit Abfahrtszeit: in Deutschland nach 12 Uhr teurer -> lieber vorher tanken (Echtpreise gelten „jetzt“,
    // also um die Tageszeit ihres Stands bereinigt)
    adjust: trip ? (s) => {
      const c = stopClock(s);
      if (c == null || !inDE(s)) return 0;
      const now = s.live && s.live.at ? new Date(s.live.at * 1000) : null;
      return FuelPlan.deHourOffset(c) - (now ? FuelPlan.deHourOffset(now.getHours() + now.getMinutes() / 60) : 0);
    } : null,
  });
  const adStations = stations.filter((s) => s.flags && s.flags.adblue);
  const adStart = veh.adtank * veh.adstart / 100;
  const adNeed = total * veh.adcons / 100;
  const ad = adStart - adNeed >= 1 || veh.adcons <= 0
    ? { ok: true, rest: adStart - adNeed, range: veh.adcons > 0 ? (adStart - 1) / (veh.adcons / 100) : Infinity }
    : { ok: false, ...planTank({ total, stations: adStations, cap: veh.adtank, consPerKm: veh.adcons / 100, reserve: 1, startL: adStart, prefer: null, avoid: veh.nomw ? isMw : null }) };
  return { diesel, ad, liters: total * veh.cons / 100, stations };
}

/* ---------------------------------------------------------------- Laenderregeln */
// Kurzhinweise ohne Gewaehr – bewusst vorsichtig formuliert.
/* Tempo je Land aus der gemeinsamen Tabelle (/api/speedrules – dieselbe nutzt RigNavi) */
let SPEED = null;
fetch("/api/speedrules").then((r) => (r.ok ? r.json() : null)).then((j) => { SPEED = j; if (route) renderAll(); }).catch(() => {});
const speedLine = (cc) => SpeedText.speedLine(SPEED, cc, veh);
/* Sondermaut (Tunnel/Bruecken): Tarife aus /api/sondermaut, Treffer liefert der Server je Route (rig.special) */
let SONDER = null;
fetch("/api/sondermaut").then((r) => (r.ok ? r.json() : null)).then((j) => { SONDER = j; if (route) renderAll(); }).catch(() => {});
/* Maut der angezeigten Route: Laender + Sonderstrecken; Datum je Etappe aus dem Fahrplan (Millau: Sommer-/Nebensaison) */
function tollFor(r) {
  const rig = (r && r.rig) || {}, cs = rig.countries || [];
  const hits = (rig.special || []).map((h) => ({ ...h, date: stageWhen(h.stage ?? (r.stage && r.stage.n)) }));
  return Toll.trip(cs, veh, (code) => (cs.find((x) => x.cc === code) || {}).days || 1, Toll.special(hits, SONDER, veh));
}

const RULES = {
  DE: (w, tr) => ({ toll: "Keine Maut für Wohnmobile – auch nicht über 3,5 t (Lkw-Maut gilt nur für Güterverkehr).",
    speed: tr ? "Gespann: 80 km/h (100 km/h nur mit Tempo-100-Zulassung)." : (w > 3.5 ? "Über 3,5 t: 100 km/h auf der Autobahn, 80 km/h außerorts." : "Bis 3,5 t: wie Pkw.") }),
  AT: (w, tr) => ({ toll: w > 3.5 ? "Über 3,5 t: fahrleistungsabhängige Maut mit GO-Box auf Autobahnen und Schnellstraßen – die Vignette gilt nicht." : "Bis 3,5 t: Vignette (digital oder Klebe) für Autobahnen/Schnellstraßen, einzelne Strecken mit Sondermaut.",
    speed: w > 3.5 ? "Über 3,5 t: meist 80 km/h Autobahn, 70 km/h Freiland." : (tr ? "Mit Anhänger: 100 km/h Autobahn, 80 km/h Freiland." : "Bis 3,5 t: wie Pkw.") }),
  SI: (w) => ({ toll: w > 3.5 ? "Über 3,5 t: elektronische Maut (DarsGo-Gerät) statt Vignette." : "Bis 3,5 t: e-Vignette für Autobahnen.", speed: "" }),
  CH: (w) => ({ toll: w > 3.5 ? "Über 3,5 t: pauschale Schwerverkehrsabgabe (PSVA) statt Vignette." : "Bis 3,5 t: Autobahnvignette.", speed: "" }),
  IT: () => ({ toll: "Autobahnmaut an Mautstellen, Klasse nach Achsen und Höhe – Gespanne meist höhere Klasse.", speed: "" }),
  FR: () => ({ toll: "Péage an Mautstellen; Wohnmobile über 3 m Höhe bzw. mit Anhänger höhere Klasse.", speed: "" }),
  CZ: (w) => ({ toll: w > 3.5 ? "Über 3,5 t: elektronische Maut (Bordgerät)." : "Bis 3,5 t: e-Vignette.", speed: "" }),
  HU: (w) => ({ toll: w > 3.5 ? "Über 3,5 t: streckenbezogene Maut HU-GO." : "Bis 3,5 t: e-Matrica.", speed: "" }),
};

/* ---------------------------------------------------------------- Karte */
// Startansicht und Grenzen: Europa (Kontinent ohne Island/Kaukasus) – weiter raus zoomen oder
// wegschieben bringt nichts, alle Kartendaten liegen hier
const EUROPE = L.latLngBounds([35.5, -10.5], [70.5, 31]);
const map = L.map("map", { preferCanvas: true, zoomSnap: 0.25,
  // Mausrad: Leaflet-Standard 60 px je Stufe war mit zoomSnap 0.25 zaeh – jetzt etwa wie die +/−-Knoepfe
  wheelPxPerZoomLevel: 22, wheelDebounceTime: 20, maxBounds: EUROPE.pad(0.35), maxBoundsViscosity: 0.9 });
// Rechtsklick / langer Druck irgendwo auf der Karte: Koordinaten dieser Stelle + in anderer App oeffnen
map.on("contextmenu", (e) => {
  L.popup().setLatLng(e.latlng)
    .setContent(`<b>📍 Diese Stelle</b><br>${placeLinks(e.latlng.lat, e.latlng.lng, "Punkt aus dem RigAnalyzer")}`).openOn(map);
});
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
window.addEventListener("load", () => setTimeout(() => { if (!route) fitEurope(); }, 50));
window.addEventListener("resize", () => { map.setMinZoom(0); map.setMinZoom(Math.max(2.5, map.getBoundsZoom(EUROPE))); });
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18, referrerPolicy: "strict-origin-when-cross-origin",
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const layers = { issues: L.layerGroup(), stops: L.layerGroup(), fuel: L.layerGroup(), camp: L.layerGroup(), shop: L.layerGroup(), way: L.layerGroup(),
  dump: L.layerGroup(), water: L.layerGroup(), steep: L.layerGroup(), radar: L.layerGroup(), lez: L.layerGroup() };
/* Regenradar: letztes RainViewer-Bild ueber den Server (/api/weather/radar), Kacheln direkt; alle 10 min neu, solange an */
let radarTimer = 0;
async function loadRadar() {
  try {
    const res = await fetch("/api/weather/radar");
    if (!res.ok) return;   // 424: Radar gerade nicht abrufbar – Ebene bleibt leer
    const r = await res.json();
    layers.radar.clearLayers();
    L.tileLayer(r.tiles, { opacity: 0.6, maxNativeZoom: 7, maxZoom: 18, zIndex: 5,
      attribution: `Radar: <a href="https://www.rainviewer.com" target="_blank" rel="noopener">RainViewer</a> (${new Date(r.time * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })})` }).addTo(layers.radar);
  } catch { /* Zusatz */ }
}
layers.radar.on("add", () => { loadRadar(); clearInterval(radarTimer); radarTimer = setInterval(loadRadar, 600000); });
layers.radar.on("remove", () => clearInterval(radarTimer));
const pin = (cls, size = 14) => L.divIcon({ className: "", html: `<div class="pin ${cls}"></div>`, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
const flagIcon = (txt, cls) => L.divIcon({ className: "", html: `<div class="flag ${cls}">${esc(txt)}</div>`, iconSize: null, iconAnchor: [0, 0] });
let cursor = null;
function moveCursor(ll) {
  if (!ll) return;
  if (!cursor) cursor = L.marker(ll, { icon: pin("cursor", 14), interactive: false, zIndexOffset: 1000 }).addTo(map);
  else cursor.setLatLng(ll);
}
function llAtKm(r, k) {
  const sg = r.segments.find((s) => k >= s.km0 && k <= s.km1) || r.segments[r.segments.length - 1];
  const t = (k - sg.km0) / Math.max(sg.km1 - sg.km0, 0.001);
  return sg.c[Math.min(sg.c.length - 1, Math.max(0, Math.round(t * (sg.c.length - 1))))];
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
          : `<b>${esc(p.name)}</b><br>${p.status} · fertig ca. ${clock(p.eta)} Uhr`, { sticky: true });
      },
    });
    gj.addTo(covLayer);
    if (fit) fitEurope();
  } catch { /* Abdeckung ist Zusatz */ }
}
$("cov-toggle").addEventListener("change", () => {
  if ($("cov-toggle").checked) { covLayer.addTo(map); if (!route) $("legend").innerHTML += AGE_LEGEND; }
  else { map.removeLayer(covLayer); if (!route) legend(); }
});

function syncToggles() {
  document.querySelectorAll("#toggles input[data-m]").forEach((cb) => { const l = layers[cb.dataset.m]; if (cb.checked) l.addTo(map); else map.removeLayer(l); });
}

function colorFor(seg, issues) {
  const layer = $("layer").value;
  if (layer === "grade") {
    const g = seg.g;
    if (g == null) return cssVar("--na");
    return g >= 12 ? cssVar("--err") : g >= 8 ? cssVar("--warn") : g >= 5 ? "#c9b84a" : "#3f9a5f";
  }
  if (layer === "stage") return STAGE_COLORS[((seg.stage || 1) - 1) % STAGE_COLORS.length];
  if (layer === "speed") {
    const v = Math.min(seg.rv || 0, veh.vmax);
    return v < 30 ? cssVar("--err") : v < 50 ? cssVar("--warn") : v < 70 ? "#c9b84a" : "#3f9a5f";
  }
  let worst = null;
  for (const i of issues) {
    if (i.sev === "info" || i.sev === "green" || i.kind === "lez") continue;   // Umweltzonen: eigene Ebene + Fazit-Block
    if (i.km1 + 0.05 >= seg.km0 && i.km0 - 0.05 <= seg.km1) { worst = i.sev === "red" ? "red" : (worst || "amber"); if (worst === "red") break; }
  }
  return worst === "red" ? cssVar("--err") : worst === "amber" ? cssVar("--warn") : "#3f9a5f";
}

function drawMap(r, issues, plan) {
  routeLayer.clearLayers();
  Object.entries(layers).forEach(([k, l]) => { if (k !== "radar") l.clearLayers(); });   // Radar bleibt
  L.polyline(r.segments.map((s) => s.c), { color: cssVar("--card"), weight: 10, opacity: .75, interactive: false }).addTo(routeLayer);
  for (const s of r.segments) {
    const tip = `km ${fmt(s.km0, 1)}–${fmt(s.km1, 1)} · ${esc(s.roadl)}${s.g != null ? ` · Steigung bis ${fmt(s.g)} %` : ""} · ca. ${fmt(Math.min(s.rv || 0, veh.vmax))} km/h`;
    L.polyline(s.c, { color: colorFor(s, issues), weight: 6, opacity: .95 }).bindTooltip(tip, { sticky: true }).addTo(routeLayer);
  }
  const first = r.segments[0].c[0], last = r.segments[r.segments.length - 1].c.slice(-1)[0];
  L.marker(first, { icon: pin("start") }).bindTooltip("Start").addTo(routeLayer);
  L.marker(last, { icon: pin("end") }).bindTooltip("Ziel").addTo(routeLayer);

  for (const i of issues) {
    if (i.sev === "info" || i.sev === "green" || i.lat == null) continue;
    const short = i.title.replace(/^(Zu niedrig|Knapp|Zu schmal|Zu schwer|Zu lang|Achslast max\.): /, "").replace(/^max\. /, "");
    const icon = i.dim ? flagIcon(short, i.sev === "red" ? "" : "a") : pin(i.sev === "red" ? "r" : "a", 11);
    L.marker([i.lat, i.lon], { icon, zIndexOffset: i.sev === "red" ? 500 : 300 })
      .bindPopup(`<b>${esc(i.title)}</b><br>${esc(i.detail)}<br>bei km ${fmt(i.km0, 1)}<br>${placeLinks(i.lat, i.lon, i.title)}`).addTo(layers.issues);
  }
  plan.diesel.stops.forEach((s, n) => {
    L.marker([s.lat, s.lon], { icon: pin("fuelstop", 18), zIndexOffset: 400 })
      .bindPopup(`<b>⛽ Tankstopp ${n + 1}: ${esc(s.name || "Tankstelle")}</b><br>bei km ${fmt(s.km, 1)} · ankommen mit ca. ${fmt(s.arrive)} l · tanken ca. ${fmt(s.liters)} l${stopExtra(s)}${s.flags ? "<br>" + fuelBadges(s.flags) : ""}`)
      .addTo(layers.stops);
  });
  for (const s of plan.stations.filter((x) => !isDetour(x))) {
    L.marker([s.lat, s.lon], { icon: pin(isMw(s) ? "fuel mw" : "fuel", 11) }).bindTooltip(`${esc(s.name || "Tankstelle")} · km ${fmt(s.km, 1)}${s.flags && s.flags.adblue ? " · AdBlue" : ""}${s.flags && s.flags.truck ? " · Lkw" : ""}${isMw(s) ? " · Autobahn (teuer)" : ""}${s.live ? ` · ${fmt(s.price, 3)} €/l (${s.live.src || "Tankerkönig"})` : ""}`)
      .bindPopup(`<div class="camppop"><b>⛽ ${esc(s.name || "Tankstelle")}</b><br>bei km ${fmt(s.km, 1)}${s.live ? ` · ${fmt(s.price, 3)} €/l (${esc(s.live.src || "Tankerkönig")} ${whenT(s.live.at)})` : ""}<br>${placeLinks(s.lat, s.lon, s.name || "Tankstelle")}${((c) => (c ? "<br>" + c : ""))(ShopList.tourChipHtml(s, "fuel"))}</div>`)
      .addTo(layers.fuel);
  }
  drawCampLayers(r);   // camps.js: nur die gefilterten Plaetze
  ShopList.drawShopLayer(r, layers.shop);   // shops.js: Einkaufen (Filter aus dem Reiter)
  WayList.drawWayLayer(r, layers.way);      // unterwegs.js: Sehenswertes, Einkehr, Service
  for (const s of (r.rig && r.rig.steep) || []) {
    L.marker([s.lat, s.lon], { icon: pin("steep", 12) }).bindTooltip(`${s.dir === "up" ? "Steigung" : "Gefälle"} bis ${fmt(s.max)} % · km ${fmt(s.km0, 1)}–${fmt(s.km1, 1)}`).addTo(layers.steep);
  }
  // Umweltzonen-Flaechen, eingefaerbt nach Urteil; je id einmal (Zone ueber zwei Etappen), Ringe = Teilflaechen
  const drawn = new Set();
  for (const i of issues) {
    if (!i.lez || !i.lez.poly || drawn.has(i.lez.id)) continue;
    drawn.add(i.lez.id);
    const z = i.lez, c = LEZ_COLOR[z.verdict.v];
    L.polygon(z.poly.map((ring) => [ring]), { color: c, weight: 1.5, fillColor: c, fillOpacity: 0.18 })
      .bindTooltip(`<b>Umweltzone ${esc(lezName(z))}</b><br>${esc(z.verdict.text)}<br><small>${esc(lezTip(z))}</small>`, { sticky: true })
      .addTo(layers.lez);
  }
  syncToggles();
  legend();
}

function legend() {
  const l = $("layer").value;
  const sw = (c, t) => `<i style="background:${c}"></i><span>${t}</span>`;
  if (l === "stage" && trip) {
    $("legend").innerHTML = trip.stages.map((_s, i) => sw(STAGE_COLORS[i % STAGE_COLORS.length], `E${i + 1}`)).join("");
    return;
  }
  $("legend").innerHTML = l === "fit"
    ? sw("#3f9a5f", "passt") + sw(cssVar("--warn"), "knapp / prüfen") + sw(cssVar("--err"), "passt nicht")
    : l === "grade" ? sw("#3f9a5f", "< 5 %") + sw("#c9b84a", "5–8 %") + sw(cssVar("--warn"), "8–12 %") + sw(cssVar("--err"), "≥ 12 %")
    : sw(cssVar("--err"), "< 30 km/h") + sw(cssVar("--warn"), "30–50") + sw("#c9b84a", "50–70") + sw("#3f9a5f", "≥ 70 km/h");
}

/* ---------------------------------------------------------------- Panel */
let route = null;
let importSnap = null, importText = null, showingImport = false;
let trip = null, combined = null, stageIdx = 0, tripPlan = null;
const STAGE_COLORS = ["#2f6fbf", "#e8590c", "#2f9e44", "#ae3ec9", "#1098ad", "#f08c00", "#c2255c", "#5c940d", "#4263eb", "#d6336c", "#0ca678", "#7048e8"];

function combine(t) {
  let off = 0;
  const c = { name: t.name, file: t.file, stats: { km: 0, source: "lokal", reconstructed: false }, segments: [], pois: [], notes: [],
    rig: { restrictions: [], hairpins: [], steep: [], profile: [], ascent: 0, descent: 0, countries: [], special: [], elevation: true },
    missing: t.missing, stageBounds: [], quality: Quality.merge(t.stages.map((s) => ({ km: s.stats.km, quality: s.quality }))) };
  const cc = {};
  for (const s of t.stages) {
    const o = off, n = s.stage.n, r = s.rig || {};
    c.stageBounds.push([o, o + s.stats.km, n]);
    c.segments.push(...s.segments.map((g) => ({ ...g, km0: g.km0 + o, km1: g.km1 + o, stage: n })));
    c.pois.push(...s.pois.map((p) => ({ ...p, km: p.km + o, stage: n })));
    (s.notes || []).forEach((x) => c.notes.push({ ...x, km: x.km + o }));
    (r.restrictions || []).forEach((x) => c.rig.restrictions.push({ ...x, km0: x.km0 + o, km1: x.km1 + o, stage: n }));
    (r.hairpins || []).forEach((x) => c.rig.hairpins.push({ ...x, km: x.km + o, stage: n }));
    (r.steep || []).forEach((x) => c.rig.steep.push({ ...x, km0: x.km0 + o, km1: x.km1 + o, stage: n }));
    (r.profile || []).forEach((p) => c.rig.profile.push([p[0] + o, p[1], p[2]]));
    (r.special || []).forEach((x) => c.rig.special.push({ ...x, km: x.km + o, stage: n }));
    // Umweltzonen: null = kein Stand -> fuer die ganze Reise null
    if (r.lez == null) c.rig.lez = null;
    else if (c.rig.lez !== null) { c.rig.lez = c.rig.lez || []; c.rig.lez.push(...r.lez.map((z) => ({ ...z, km0: z.km0 + o, km1: z.km1 + o, stage: n }))); }
    c.rig.ascent += r.ascent || 0; c.rig.descent += r.descent || 0;
    c.rig.elevation = c.rig.elevation && !!r.elevation;
    for (const k of r.countries || []) {
      if (!cc[k.cc]) cc[k.cc] = { ...k, km: 0, motorway_km: 0, toll_km: 0, days: 0 };
      const e = cc[k.cc];
      e.km += k.km; e.motorway_km += k.motorway_km; e.days += 1;   // jede Etappe im Land = ein Reisetag
      e.toll_km = e.toll_km == null || k.toll_km == null ? null : e.toll_km + k.toll_km;   // alte Etappe ohne Maut-km
    }
    if (s.stats.source !== "lokal") c.stats.source = "teils live";
    c.stats.reconstructed = c.stats.reconstructed || !!s.stats.reconstructed;
    off += s.stats.km;
  }
  c.stats.km = off;
  c.rig.countries = Object.values(cc).sort((a, b) => b.km - a.km);
  c.notes = ShopList.dedupeHints(c.notes);   // Laden-Hinweis nur einmal je Reise, nicht je Etappe
  return c;
}

function levelAt(levels, km) {
  // Fuellstand bei km (nach einem Tankstopp genau dort: der volle Wert)
  let v = levels[0][1];
  for (let i = 1; i < levels.length; i++) {
    const [k0, l0] = levels[i - 1], [k1, l1] = levels[i];
    if (km < k0) break;
    if (k1 === k0) { if (km >= k1) v = l1; continue; }
    if (km <= k1) return l0 + (l1 - l0) * (km - k0) / (k1 - k0);
    v = l1;
  }
  return v;
}

/* ---------------------------------------------------------------- Fahrplan der Reise */
const tripKey = () => `rig.trip.${trip ? trip.name : ""}`;
function tripCfg() {
  let c = null;
  try { c = JSON.parse(localStorage.getItem(tripKey()) || "null"); } catch { c = null; }
  // Fahrpausen gehoeren zur Tour (wandern so ins Navi) – eine geoeffnete/gewaehlte Tour gewinnt
  const tp = window.planner && window.planner.pause ? window.planner.pause() : null;
  return { date: "", time: "08:30", nights: {}, pause: "auto", ...(c || {}), ...(tp ? { pause: tp } : {}) };
}
function saveTripCfg(c) { try { localStorage.setItem(tripKey(), JSON.stringify(c)); } catch { /* privat */ } }
const WD = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
function dayLabel(start, d) {
  if (!start) return `Tag ${d + 1}`;
  const t = new Date(start.getTime() + d * 86400000);
  return `${WD[t.getDay()]} ${String(t.getDate()).padStart(2, "0")}.${String(t.getMonth() + 1).padStart(2, "0")}.`;
}
const hhmm = (h) => { const m = Math.round(h * 60); return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };
const shortStage = (nm) => (nm.split(": ").slice(1).join(": ") || nm).replace(/Camping(platz)? |Domaine d'|TCS /g, "");

// Aufenthalte je Etappe: nur fuer eine Route aus dem Planer, deren Etappen zu dieser Reise passen
function tripStays() {
  const all = window.plannerResult && window.planner && window.planner.stays ? window.planner.stays() : [];
  return trip && all.length === trip.stages.length ? all : trip ? trip.stages.map(() => []) : [];
}

function schedule() {
  const cfg = tripCfg();
  const stays = tripStays();
  const start = cfg.date ? new Date(cfg.date + "T00:00") : null;
  const [hh, mm] = (cfg.time || "08:30").split(":").map(Number);
  const dep = hh + (mm || 0) / 60;
  let day = 0;
  const last = trip.stages.length - 1;
  return { cfg, start, rows: trip.stages.map((s, i) => {
    const km = s.stats.km, drive = travelHours(s);
    const pause = Aufenthalt.pauseHours(km, drive, cfg.pause);   // „auto“ oder fest gewählt
    const st = stays[i] || [], stay = Aufenthalt.sumMin(st) / 60;
    const nights = cfg.nights[i] ?? s.stage.nights ?? (i === last ? 0 : 1);
    const ev = evaluate(s);
    const row = { i, s, km, drive, pause, stay, stays: st, day, dep, arr: dep + drive + pause + stay, nights,
      red: ev.filter((x) => x.sev === "red" && x.kind !== "lez").length, amber: ev.filter((x) => x.sev === "amber" && x.kind !== "steep" && x.kind !== "lez").length,
      lez: ev.filter((x) => x.kind === "lez" && x.lez), lezNa: ev.some((x) => x.kind === "lez" && !x.lez),
      stops: tripPlan ? tripPlan.diesel.stops.filter((st) => st.stage === s.stage.n).length : 0 };
    day += Math.max(nights, 0);
    return row;
  }) };
}

/* Wetter je Etappe: Stuetzpunkte aus Abfahrt + Fahrzeit, /api/weather, Ergebnis je Etappe unter der Zeile */
let wxKey = "", wxData = null, wxTimer = 0;
function weatherRequest(rows, start) {
  const base = start ? new Date(start) : (() => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; })();
  const totalKm = rows.reduce((a, r) => a + r.km, 0);
  const step = Math.max(50, Math.ceil(totalKm / Math.max(1, 40 - 2 * rows.length)));   // hoechstens 40 Punkte
  const stages = rows.map((r) => {
    const line = r.s.segments.map((sg) => [sg.c[0][0], sg.c[0][1], sg.km0]);
    const lastSeg = r.s.segments[r.s.segments.length - 1];
    line.push([lastSeg.c[lastSeg.c.length - 1][0], lastSeg.c[lastSeg.c.length - 1][1], lastSeg.km1]);
    const day = base.getTime() + r.day * 864e5;
    const pts = WeatherUI.stagePoints(line, day + r.dep * 36e5, day + (r.arr - r.stay) * 36e5, step);
    return { n: r.i + 1, pts: Aufenthalt.shiftPoints(pts, r.stays, line[0][2]) };   // Aufenthalte verschieben
  });
  return { rig: !!veh.trailer || veh.zgg > 3.5, stages };
}
function fillWeather() {
  const note = $("wx-note");
  if (!wxData || !note) return;
  wxData.stages.forEach((st) => {
    const row = $(`wx-${st.n - 1}`);
    if (!row) return;
    const t = WeatherUI.stripText(st), w = WeatherUI.warnRows(st.warnings);
    row.children[1].innerHTML = `<span class="wx-main">${esc(t.main)}</span>${t.sub ? `<span class="wx-sub${t.level ? " g-" + t.level : ""}">${esc(t.sub)}</span>` : ""}`
      + w.map((x) => `<span class="wx-warn ${x.cls}" title="${esc(x.source)}">${esc(x.text)}</span>`).join("");
  });
  const err = Object.values(wxData.errors || {});
  note.innerHTML = `${tripCfg().date ? "" : "Wetter für Start heute (kein Datum gesetzt) · "}${err.length ? esc(err.join(" · ")) + " · " : ""}${esc(wxData.attribution || "")}`;
}
function scheduleWeather(rows, start) {
  const req = weatherRequest(rows, start);
  const key = JSON.stringify(req);
  if (key === wxKey) { fillWeather(); return; }
  clearTimeout(wxTimer);
  wxTimer = setTimeout(async () => {
    try {
      const r = await fetch("/api/weather", { method: "POST", headers: { "Content-Type": "application/json" }, body: key });
      if (!r.ok) throw new Error(r.status);
      wxData = await r.json(); wxKey = key;
    } catch { wxData = { stages: [], errors: { all: "Wetter gerade nicht abrufbar" }, attribution: "" }; }
    fillWeather();
  }, 800);
}

// Fahrplan: ein Symbol je Etappe mit Umweltzone (schlechtestes Urteil), Namen im title
function lezFlag(r) {
  if (r.lezNa) return `<span title="Umweltzonen gerade nicht verfügbar">⚪ UWZ</span>`;
  if (!r.lez.length) return "";
  const worst = r.lez.some((x) => x.lez.verdict.v === "no") ? "no" : r.lez.some((x) => x.lez.verdict.v === "check") ? "check" : "ok";
  const t = r.lez.map((x) => `Umweltzone ${lezName(x.lez)}: ${x.lez.verdict.text}`).join("\n");
  return `<span class="lez-flag" title="${esc(t)}">${LezUI.ICON[worst]} UWZ</span>`;
}

// Etappen-Hinweis „letzter Einkauf vor dem Ziel“ (shops.js, Text aus st.last_shop); eigene Zeile unter der Etappe
function shopRow(r) {
  const t = ShopList.stageHint(r.s.last_shop);
  return t ? `<tr class="shopline"><td></td><td colspan="5"><small>${esc(t)}</small></td></tr>` : "";
}

function renderTrip() {
  const box = $("trip");
  if (!trip) { box.hidden = true; return; }
  box.hidden = false;
  const { cfg, start, rows } = schedule();
  const tot = rows.reduce((a, r) => ({ km: a.km + r.km, h: a.h + r.drive }), { km: 0, h: 0 });
  const rest = rows.slice(0, -1).reduce((a, r) => a + Math.max(r.nights - 1, 0), 0);
  const nDays = rows.length ? rows[rows.length - 1].day + 1 : 0;
  const bar = `<div class="stagebar"><button data-stage="0" aria-pressed="${stageIdx === 0}">Gesamt</button>${rows.map((r) => `<button data-stage="${r.i + 1}" aria-pressed="${stageIdx === r.i + 1}">${r.i + 1}</button>`).join("")}</div>`;
  const when = `<div class="when"><label>Start <input type="date" id="t-date" value="${esc(cfg.date)}"></label><label>Abfahrt jeweils <input type="time" id="t-time" value="${esc(cfg.time)}"></label><label>Fahrpausen <select id="t-pause">${
    Object.entries(Aufenthalt.PAUSE).map(([k, v]) => `<option value="${k}"${(cfg.pause || "auto") === k ? " selected" : ""}>${v}</option>`).join("")}</select></label><span class="hint">${cfg.date ? "" : "ohne Datum: Tag 1, Tag 2 …"}</span></div>`;
  const body = rows.map((r) => {
    const cls = r.arr <= 15.5 ? "arr-g" : r.arr <= 16 ? "arr-a" : "arr-r";
    // Fahrplan einzeilig: Name gekuerzt (voll im title), Datum/„weiter“ nur mit Startdatum, feste Hinweis-Symbole
    const next = r.i < rows.length - 1 && r.nights > 0 ? ` · weiter ${dayLabel(start, r.day + r.nights)}` : "";
    const sub = cfg.date ? `<small>${dayLabel(start, r.day)}${next}</small>` : "";
    const hint = (t, txt) => `<span title="${esc(t)}">${txt}</span>`;
    const flags = [r.red ? hint(`${r.red} Stelle${r.red > 1 ? "n" : ""} passt nicht`, `🔴 ${r.red}`) : "",
      r.amber ? hint(`${r.amber} Stelle${r.amber > 1 ? "n" : ""} knapp`, `🟠 ${r.amber}`) : "",
      !r.red && !r.amber ? hint("alles passt", "✓") : "",
      lezFlag(r),
      r.stops ? hint(`${r.stops} Tankstopp${r.stops > 1 ? "s" : ""}`, `⛽ ${r.stops}`) : ""].filter(Boolean).join(" ");
    const full = r.s.stage.name || "";
    return `<tr class="row${stageIdx === r.i + 1 ? " sel" : ""}" data-stage="${r.i + 1}"><td class="n">${r.i + 1}</td>
      <td class="st"><span class="nm" title="${esc(full)}">${esc(shortStage(full))}</span>${sub}</td>
      <td class="n">${fmt(r.km)} km<small>${hm(r.drive)} + ${Math.round(r.pause * 60)}′${r.stay ? `<span title="${esc(r.stays.map((x) => `${x.name || "Zwischenpunkt"}: ${Aufenthalt.fmt(x.min)}`).join(" · "))}"> + ⏱ ${Aufenthalt.fmt(r.stay * 60)}</span>` : ""}</small></td>
      <td class="n">${hhmm(r.dep)}<small class="${cls}">an ${hhmm(r.arr)}</small></td>
      <td class="n"><span class="nights" role="group" aria-label="Nächte am Ziel">${r.i === rows.length - 1 ? "<b>Ziel</b>"
        : `<button type="button" data-nights-step="${r.i}" data-d="-1" aria-label="eine Nacht weniger">−</button><b>${r.nights}</b><button type="button" data-nights-step="${r.i}" data-d="1" aria-label="eine Nacht mehr">+</button>`}</span></td>
      <td class="hints">${flags}</td></tr>
      ${shopRow(r)}
      <tr class="wx" id="wx-${r.i}"><td></td><td colspan="5"></td></tr>`;
  }).join("");
  // eine lange Einzelstrecke wurde fuer die Pruefung in Etappen geteilt
  const split = trip.split ? `<p class="hint split-note">✂ Die Strecke ist ${fmt(trip.split.km)} km lang – für die Prüfung in ${trip.split.parts} Etappen geteilt (je höchstens ${fmt(trip.split.stage_km)} km), wo möglich an einem Ort. Nächte am Etappenziel stellt ihr unter „Nächte“ ein.</p>` : "";
  box.innerHTML = `<h3>🗓️ Fahrplan</h3>${split}${bar}${when}
    <table class="ttab"><thead><tr><th>#</th><th>Etappe</th><th class="n">Strecke</th><th class="n">ab / an</th><th class="n">Nächte</th><th></th></tr></thead>
    <tbody>${body}<tr class="tot"><td></td><td>${rows.length} Etappen, ${rest} Standtag${rest === 1 ? "" : "e"} · ${nDays} Tage</td><td class="n">${fmt(tot.km)} km<small>${hm(tot.h)} Fahrt</small></td><td></td><td></td><td></td></tr></tbody></table>
    <div class="legend2">an <span class="arr-g">■</span> bis 15:30 <span class="arr-a">■</span> bis 16:00 <span class="arr-r">■</span> später</div>
    <div class="wx-note" id="wx-note"></div>
    <p class="hint">Fahrzeit mit max. ${veh.vmax} km/h und dem Tempo der Strecke, dazu ${{ auto: "45–105 Minuten Pause je nach Länge", "2h20": "20 Minuten Pause nach je 2 Stunden Fahrt", none: "keine Pausen" }[cfg.pause] || "45–105 Minuten Pause je nach Länge"}${rows.some((r) => r.stay) ? " und die Aufenthalte (⏱) aus dem Planer" : ""}. Baustellen, Maut und Tankstopps mit Anhänger kosten oft noch mehr – die Ankunftszeiten sind der gute Fall.</p>`;
  scheduleWeather(rows, start);
}

function selectStage(i) {
  stageIdx = i;
  route = i === 0 ? combined : trip.stages[i - 1];
  renderAll();
  LayoutUI.fitWhenSettled(map, L.latLngBounds(route.segments.flatMap((s) => s.c)));
}
let tab = "issues";

function fuelBadges(f) {
  if (!f) return "";
  const b = [["Diesel", f.diesel !== false], ["AdBlue", f.adblue], ["Lkw-Säule", f.truck], ["LPG", f.lpg]];
  const cls = f.cls && !f.motorway && f.cls !== "frei" ? `<i class="cls-${f.cls}">${FuelPlan.LABEL[f.cls]}</i>` : "";
  return `<span class="chips2">${b.map(([t, on]) => `<i class="${on ? "on" : ""}">${t}</i>`).join("")}${f.motorway ? `<i class="mw">Autobahn · teuer</i>` : ""}${cls}${f.detour_km ? `<i class="det">Abstecher</i>` : ""}</span>`;
}

// Preis (geschaetzt oder echt) und Umweg eines Tankstopps
function stopExtra(s) {
  const p = s.live
    ? ` · ${fmt(s.price, 3)} €/l (${s.live.src || "Tankerkönig"} ${whenT(s.live.at)}${s.live.open === false ? ", gerade geschlossen" : ""})`
    : ` · ≈ ${fmt(s.price, 2)} €/l${s.estimated ? (s.localBase != null ? " (geschätzt nach echten Preisen in der Nähe)" : " (geschätzt)") : ""}`;
  const clock = trip && inDE(s) ? stopClock(s) : null;
  const tod = clock == null ? "" : ((h) => h >= 12 && h < 19
    ? ` · <span class="tod bad">⏰ an ca. ${hhmm(clock)} – in Deutschland steigen die Preise um 12 Uhr (Diesel im Schnitt +18 ct), vor 12 tanken lohnt</span>`
    : h >= 10 && h < 12 ? ` · <span class="tod good">⏰ an ca. ${hhmm(clock)} – gute Zeit, kurz vor 12 ist es am günstigsten</span>`
    : ` · ⏰ an ca. ${hhmm(clock)}`)(((clock % 24) + 24) % 24);
  const links = (s.live && PRICE_SITE[s.live.cc || "DE"] ? extLink(PRICE_SITE[s.live.cc || "DE"], "Preisportal") : "")
    + placeLinks(s.lat, s.lon, s.name || "Tankstelle");
  if (!isDetour(s)) return p + tod + links;
  return `${p} · Abstecher ca. ${fmt(s.flags.detour_km, 1)} km / ${fmt(s.detour_min)} min ab der Ausfahrt`
    + (s.saving != null && s.saving > 0.5 ? ` · spart ca. ${fmt(s.saving, 0)} € gegenüber direkt an der Strecke` : "") + tod + links;
}

function renderAll() {
  const r = route;
  if (!r) return;
  $("result").hidden = false;
  const issues = evaluate(r);
  if (trip) tripPlan = fuelPlan(combined);
  const stageStart = trip && stageIdx > 0 ? levelAt(tripPlan.diesel.levels, combined.stageBounds[stageIdx - 1][0]) : null;
  const plan = trip && stageIdx === 0 ? tripPlan : fuelPlan(r, stageStart);
  renderTrip();
  ensurePrices(trip ? tripPlan : plan);
  if (typeof ensureFrCamps === "function") ensureFrCamps();   // camps.js: Plaetze der franz. Tourismusaemter
  if (typeof ensureLaenge === "function") ensureLaenge();   // camps.js: Stellplatz-Laenge aus Website/Luftbild
  const hours = travelHours(r);
  const rig = r.rig || {};

  $("r-name").textContent = trip && stageIdx > 0 ? r.stage.name : r.name;
  $("r-sub").textContent = `${r.file} · ${r.stats.source === "lokal" ? "lokale Kartendaten" : "Kartendaten " + r.stats.source}${r.stats.reconstructed ? " · Straßenverlauf ergänzt" : ""}${rig.elevation ? "" : " · ohne Höhenprofil"}`;
  const tollTotal = tollFor(r).total;
  const tiles = [
    [`${fmt(r.stats.km, 0)} km`, "Länge"],
    [hm(hours), `Fahrzeit bei max. ${veh.vmax} km/h`],
    [`${fmt(plan.liters, 0)} l`, `${veh.price ? `Diesel ≈ ${fmt(plan.liters * veh.price, 0)} €` : "Diesel"}${tollTotal ? ` · Maut ${tollTotal}` : ""}`],
    [String(plan.diesel.stops.length), plan.diesel.stops.length === 1 ? "Tankstopp" : "Tankstopps"],
    [rig.ascent != null ? `${fmt(rig.ascent)} hm` : "–", "bergauf"],
    [String(issues.filter((i) => i.sev === "red" && i.kind !== "lez").length), "Stellen passen nicht"],
  ];
  $("tiles").innerHTML = tiles.map(([b, s]) => `<div class="tile"><b>${esc(b)}</b><small>${esc(s)}</small></div>`).join("");
  renderImportCompare(trip || route);

  // Fazit
  // Umweltzonen zaehlen hier nicht als Engstelle – sie stehen im Umweltzonen-Block darunter und in der Liste
  const red = issues.filter((i) => i.sev === "red" && i.kind !== "lez"), amber = issues.filter((i) => i.sev === "amber" && i.kind !== "lez");
  const v = $("verdict");
  v.className = `card verdict ${red.length ? "red" : amber.length ? "amber" : "green"}`;
  const head = red.length ? `🚫 ${red.length} Stelle${red.length > 1 ? "n" : ""}, an denen ihr laut Karte nicht durchkommt`
    : amber.length ? `⚠️ Passt – aber ${amber.length} Stelle${amber.length > 1 ? "n" : ""} wird es knapp oder ist zu prüfen`
    : "✅ Keine bekannten Hindernisse für euer Fahrzeug";
  const amberNoSteep = amber.filter((i) => i.kind !== "steep");
  const steepCount = amber.length - amberNoSteep.length;
  const shown = [...red, ...amberNoSteep].slice(0, 12);
  v.innerHTML = `<h3>${head}</h3><p>Geprüft gegen ${fmt(veh.height, 2)} m Höhe, ${fmt(veh.width, 2)} m Breite, ${fmt(veh.length, 1)} m Länge, ${fmt(veh.weight, 1)} t${veh.trailer ? ", mit Anhänger" : ""}. Grundlage: OpenStreetMap – fehlende Schilder sind möglich.</p>`
    + shown.map((i) => `<div class="issue" data-ll="${i.lat},${i.lon}"${i.tip ? ` title="${esc(i.tip)}"` : ""}><span class="km">km ${fmt(i.km0, 1)}</span><span><span class="sev ${i.sev}"></span>${esc(i.title)}<small>${esc(i.detail)}</small></span></div>`).join("")
    + (steepCount ? `<p class="hint" style="margin-top:8px">Dazu ${steepCount} steile Abschnitt${steepCount > 1 ? "e" : ""} – siehe „Steigungen &amp; Gefälle“.</p>` : "")
    + (red.length + amberNoSteep.length > shown.length ? `<p class="hint">… weitere im Reiter „Engstellen &amp; Hinweise“.</p>` : "")
    + lezBlock(issues);

  const d = plan.diesel;
  let fh = `<p class="hint">Reichweite mit vollem Tank ca. <b>${fmt((veh.tank - veh.reserve) / (veh.cons / 100))} km</b> bis zur Reserve (${fmt(veh.reserve)} l). Start mit ${fmt(d.levels[0][1])} l${trip && stageIdx > 0 ? " (Stand nach der Vor-Etappe)" : ""}.</p>`;
  if (!d.stops.length) fh += `<div class="ok-box">Kein Tankstopp nötig – ihr kommt mit ca. <b>${fmt(Math.max(d.finish, 0))} l</b> im Tank an.</div>`;
  fh += d.stops.map((s, n) => `<div class="stop" data-ll="${s.lat},${s.lon}"><span class="km">km ${fmt(s.km, 1)}</span><span><b>${n + 1}. ${esc(s.name || "Tankstelle")}</b>${fuelBadges(s.flags)}<small>${s.stage && stageIdx === 0 ? `Etappe ${s.stage} · ` : `nach ca. ${hm(s.tmin / 60)} Fahrzeit · `}${isDetour(s) ? "" : `${fmt(s.off)} m abseits · `}ankommen mit ca. ${fmt(s.arrive)} l → <b>${fmt(s.liters)} l tanken</b>${stopExtra(s)}${s.flags && s.flags.hours ? ` · ${esc(s.flags.hours)}` : ""}</small><div class="gauge"><span style="width:${Math.max(0, Math.min(100, s.arrive / veh.tank * 100))}%"></span></div></span></div>`).join("");
  if (d.stops.length) fh += `<p class="hint">Ankunft am Ziel mit ca. ${fmt(Math.max(d.finish, 0))} l.</p>`;
  for (const g of d.gaps) fh += `<div class="note-box">⚠️ Zwischen km ${fmt(g.from, 0)} und km ${fmt(g.to, 0)} liegt keine Tankstelle in Reichweite (mit ${fmt(veh.reserve)} l Reserve). Vorher volltanken!</div>`;
  if (plan.ad.ok) fh += `<div class="ok-box">AdBlue: reicht – ca. ${fmt(Math.max(plan.ad.rest, 0), 1)} l übrig, Reichweite insgesamt etwa ${fmt(plan.ad.range)} km.</div>`;
  else {
    fh += `<div class="note-box">AdBlue: nachfüllen nötig – ${plan.ad.stops.length ? plan.ad.stops.map((s) => `bei km ${fmt(s.km, 0)} (${esc(s.name || "Tankstelle")})`).join(", ") : "keine Tankstelle mit AdBlue-Angabe an der Strecke, Kanister mitnehmen"}.</div>`;
  }
  const near = plan.stations.filter((s) => !isDetour(s)), det = plan.stations.length - near.length;
  const ad = near.filter((s) => s.flags && s.flags.adblue).length, tr = near.filter((s) => s.flags && s.flags.truck).length;
  const mw = near.filter(isMw).length, mwStops = d.stops.filter(isMw).length;
  // die langen Erklaerungen stehen im Reiter „⛽ Sprit-Info“, im Tankplan nur eine Zeile
  let info = `<p class="hint">${near.length} Tankstellen bis 500 m an der Strecke, davon ${ad} mit AdBlue und ${tr} mit Lkw-Säule (laut Karte – oft unvollständig)`
    + (mw ? ` · ${mw} direkt an der Autobahn${veh.nomw ? (mwStops ? `, ${mwStops} davon im Plan, weil nichts anderes in Reichweite war` : " – im Plan gemieden") : ""}` : "")
    + (det ? ` · dazu ${det} bis 3 km hinter Autobahn-Ausfahrten, dort ist es oft günstiger` : "") + `.</p>`
  info += `<p class="hint">${liveInfo ? `Echte Dieselpreise für ${liveInfo.n} Tankstellen (${Object.entries(liveInfo.per).map(([c, k]) => `${CC_DE[c] || c} ${k}`).join(", ")}) – ${esc(liveInfo.source)}; die übrigen sind geschätzt` : "Preise sind geschätzt"}: euer Literpreis aus dem Fahrzeugprofil (${fmt(veh.price || PRICE_BASE, 2)} €) mit Auf- und Abschlägen je Art – Raststätte +${fmt(FuelPlan.DELTA.rast, 2)} €, Marke/Autohof +${fmt(Math.min(FuelPlan.DELTA.marke, FuelPlan.DELTA.autohof), 2)}–${fmt(Math.max(FuelPlan.DELTA.marke, FuelPlan.DELTA.autohof), 2)} €, freie/Supermarkt ${fmt(FuelPlan.DELTA.discount, 2)} €. Ein Abstecher zählt nur, wenn er Sprit und Zeit (15 €/h) wieder hereinholt.</p>`;
  const avgCc = Object.keys(liveAvg);
  if (avgCc.length) info += `<p class="hint">Ohne freie Preise je Tankstelle (${avgCc.map((c) => CC_DE[c] || c).join(", ")}) rechnet der Plan statt mit eurem Profilpreis mit dem amtlichen Landesschnitt für Diesel: `
    + avgCc.map((c) => `${CC_DE[c] || c} ${fmt(liveAvg[c], 2)} €/l`).join(", ")
    + ` (EU Weekly Oil Bulletin bzw. Statistisk sentralbyrå, je Tankstellenart mit Auf-/Abschlag) – als Schätzung, nicht als echter Preis.</p>`;
  $("fuelinfo").innerHTML = info;
  fh += `<p class="hint">${near.length} Tankstellen an der Strecke · ${liveInfo ? `echte Preise für ${liveInfo.n}` : "Preise geschätzt"} – `
    + `<button type="button" class="linkbtn" data-goto-tab="fuelinfo">mehr unter ⛽ Sprit-Info</button></p>`;
  if (d.stops.length) fh += `<div class="fuel-live"><button type="button" class="ghost small" id="fuel-refresh"${liveBusy ? " disabled" : ""}>${liveBusy ? "prüfe Preise …" : "↻ Preise jetzt prüfen"}</button>`
    + `${liveChecked ? `<span class="hint">zuletzt geprüft ${hhmmT(liveChecked)}</span>` : ""}`
    + `${livePending.length ? `<span class="hint">· Preise für ${livePending.map((c) => CC_DE[c] || c).join(" und ")} werden noch geladen …</span>` : ""}</div>`;
  $("fuelplan").innerHTML = fh;

  const st = rig.steep || [];
  let sh = "";
  if (!rig.elevation) sh = `<p class="hint">Höhenprofil gerade nicht verfügbar.</p>`;
  else {
    sh = `<p class="hint steep-head">Bergauf ${fmt(rig.ascent || 0)} hm, bergab ${fmt(rig.descent || 0)} hm. Abschnitte ab 7 % und 250 m Länge:</p>`;
    const row = (s) => `<div class="issue" data-ll="${s.lat},${s.lon}"><span class="km">km ${fmt(s.km0, 1)}</span><span><span class="sev ${s.max >= 12 ? "red" : s.max >= 9 ? "amber" : "info"}"></span>${s.dir === "up" ? "⬆️ Steigung" : "⬇️ Gefälle"} bis ${fmt(s.max)} %<small>${fmt(s.len / 1000, 1)} km, im Schnitt ${fmt(s.avg)} % · ${fmt(s.ele0)} → ${fmt(s.ele1)} m</small></span></div>`;
    const big = st.filter((x) => x.max >= 9), small = st.filter((x) => x.max < 9);
    sh += st.length ? (big.map(row).join("") || `<div class="ok-box">Nichts über 9 % – nur mäßige Steigungen.</div>`)
      + (small.length ? `<details><summary class="hint">${small.length} weitere zwischen 7 und 9 % anzeigen</summary>${small.map(row).join("")}</details>` : "")
      : `<div class="ok-box">Keine längeren Steigungen über 7 %.</div>`;
  }
  $("steep").innerHTML = sh;
  // ein Reiter neben Engstellen/Plaetzen/Tankstellen (scrollt wie die Liste) – Zahl der steilen Stuecke im Reiter
  const nBig = st.filter((x) => x.max >= 9).length;
  $("tab-steep").textContent = `⛰️ Steigungen${rig.elevation ? ` (${nBig || st.length})` : ""}`;

  const cs = rig.countries || [];
  // Maut je Land: Betrag aus Maut-km × Klassensatz bzw. Vignette/Pauschale, sonst der Kurzhinweis
  const tolls = tollFor(r);
  // Anhaenger/Tempo 100 nur noch an EINER Stelle (Fahrzeugprofil) – hier nur anzeigen + Sprung dorthin
  $("rules").innerHTML = `<div class="toll-veh"><span class="hint">Gerechnet für <b>${esc(store.active)}</b>${veh.zgg > 3.5 ? " (über 3,5 t)" : ""}`
    + ` · ${veh.trailer ? "mit Anhänger" : "ohne Anhänger"}${veh.trailer && veh.tempo100 ? " · Tempo-100-Zulassung" : ""}</span>`
    + `<button type="button" class="linkbtn" data-veh-edit="v-trailer">ändern</button></div>`
    + (tolls.total ? `<div class="toll-sum">Maut &amp; Vignetten: <b>${esc(tolls.total)}</b>${tolls.approx
    ? '<span class="hint"> · Streckenmaut geschätzt aus den mautpflichtigen km</span>' : ""}</div>` : "")
    + (cs.length ? cs.map((c, i) => {
    const f = RULES[c.cc];
    const rr = f ? f(veh.zgg, veh.trailer) : { toll: "Maut-/Vignettenregeln vor der Reise prüfen.", speed: "" };
    const t = tolls.rows[i] || {};
    const toll = t.text ? `<span class="toll-amt${t.exact ? " exact" : ""}">${esc(t.text)}</span> <small>${esc(t.detail)}</small>`
      : c.motorway_km ? esc(rr.toll) : "Keine Autobahn/Schnellstraße auf dieser Route – Vignette bzw. Autobahnmaut entfällt in der Regel.";
    const sp = tolls.special.filter((x) => x.cc === c.cc).map((x) => `<div class="toll-special">${/Viaduc|Brücke|Autobahn/.test(x.name) ? "🌉" : "🚇"} <b>${esc(x.name)}</b> (km ${fmt(x.km)}): `
      + `<span class="toll-amt${x.exact ? " exact" : ""}">${esc(x.text)}</span> <small>${esc(x.detail)}${x.quelle ? ` · <a href="${esc(x.quelle)}" target="_blank" rel="noopener">Tarif</a>` : ""}</small></div>`).join("");
    return `<div class="rule"><b>${esc(c.name)} · ${fmt(c.km)} km${c.motorway_km ? `, davon ${fmt(c.motorway_km)} km Autobahn/Schnellstraße` : ""}${c.toll_km ? `, ${fmt(c.toll_km)} km mautpflichtig` : ""}</b>${toll}${sp}${speedLine(c.cc) || rr.speed ? `<br><small>${esc(speedLine(c.cc) || rr.speed)}</small>` : ""}</div>`;
  }).join("") : `<p class="hint">Länder nicht ermittelbar.</p>`) + `<p class="hint">Beträge ohne Gewähr (Stand ${Toll.TOLL.meta.stand}): Streckenmaut ist eine Schätzung je Fahrzeugklasse, Vignetten, Pauschalen und die Tunnel/Brücken mit Sondermaut sind die Preise der Betreiber (mit „ca.“: nicht amtlich bestätigt). Regeln ändern sich, vor der Reise prüfen.</p>`;

  $("quality-card").hidden = !r.quality;
  if (r.quality) {
    $("q-sum").textContent = `${r.quality.grade} · ${r.quality.score}/100`;
    $("quality").innerHTML = Quality.html(r.quality);
  }

  renderList(issues, plan);
  drawMap(r, issues, plan);
  drawProfile(r, plan);
}

/* Umweltzonen-Zusammenfassung im Fazit: nicht verfuegbar / Liste der Zonen / „umfahren?“ (nur Planer-Route) */
function lezBlock(issues) {
  const all = issues.filter((i) => i.kind === "lez");
  if (!all.length) return "";
  if (all.some((i) => !i.lez)) return `<div class="lez-box"><b>🌿 Umweltzonen gerade nicht verfügbar</b> – bitte vor der Reise selbst prüfen.</div>`;
  const pl = window.planner || {};
  const avoided = new Set(pl.avoidedZones ? pl.avoidedZones() : []);
  const no = all.filter((i) => i.lez.verdict.v === "no" && !i.lez.verdict.need);
  const names = [...new Set(no.map((i) => i.lez.name))];
  const fresh = no.filter((i) => !avoided.has(i.lez.id));
  let h = `<div class="lez-box"><b>🌿 Umweltzonen auf der Route:</b> ${[...new Map(all.map((i) => [i.lez.id, i.lez])).values()]
    .map((z) => `<span class="lez-chip ${z.verdict.v}" title="${esc(z.verdict.text)}">${LezUI.ICON[z.verdict.v]} ${esc(lezName(z))}</span>`).join(" ")}`;
  if (names.length) {
    const q = `${names.length > 1 ? "Umweltzonen" : "Umweltzone"} ${names.map((n) => lezName({ name: n })).join(", ")} umfahren?`;
    if (fresh.length && pl.avoidZones && window.plannerResult) h += `<p class="lez-ask">${esc(q)} <button type="button" class="ghost small" id="lez-avoid">umfahren</button></p>`;
    else if (fresh.length) h += `<p class="lez-ask">${esc(q)} <span class="hint">Umfahren geht nur mit einer Route aus „Route planen“ – eine hochgeladene GPX bitte im eigenen Routenplaner ändern.</span></p>`;
    else h += `<p class="hint">Liegt trotz „umfahren“ noch auf der Route.</p>`;
  }
  const note = pl.lezNote ? pl.lezNote() : null;
  if (note && window.plannerResult) h += `<p class="hint">${esc(note)}</p>`;
  return h + `</div>`;
}

function renderList(issues, plan) {
  const r = route;
  document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === tab));
  $("steep").hidden = tab !== "steep";
  $("fuelinfo").hidden = tab !== "fuelinfo";
  $("list").hidden = tab === "steep" || tab === "fuelinfo";
  let items = [];
  if (tab === "issues") {
    items = issues.map((i) => [i.km0, `<span class="sev ${i.sev}"></span>${esc(i.title)}`, i.detail, i.lat, i.lon, i.tip]);
    for (const n of r.notes || []) items.push([n.km, `<span class="sev info"></span>${esc(n.label)}`, n.text, n.lat, n.lon]);
    items.sort((a, b) => a[0] - b[0]);
  }
  renderCampFilters();
  ShopList.renderShopFilters($("shop-filters"), window.shopsChanged);
  WayList.renderWayFilters($("way-filters"), window.wayChanged);
  if (tab === "steep" || tab === "fuelinfo") return;   // Inhalt kommt aus renderRules (#steep, #fuelinfo)
  if (tab === "camp") { renderCampList(r); return; }
  if (tab === "shop") { ShopList.renderShopList(r, $("list")); return; }
  if (tab === "way") { WayList.renderWayList(r, $("list")); return; }
  if (tab === "fuel") {
    // Preis je Tankstelle: echt mit Quelle, sonst Knopf „Preis holen“ (einzeln, auf Nachfrage)
    const priceTag = (s) => s.live
      ? `<span class="pr live">${fmt(s.price, 3)} €/l <i>${esc(s.live.src || "Tankerkönig")} ${whenT(s.live.at)}${s.live.open === false ? " · zu" : ""}</i></span>`
      : liveNone.has(s.osm) ? `<span class="pr none">kein Preis verfügbar</span>`
      : s.osm ? `<button type="button" class="ghost small price-btn" data-osm="${esc(s.osm)}" data-ll="${s.lat},${s.lon}">€ Preis holen</button>` : "";
    items = plan.stations.map((s) => [s.km, `${esc(s.name || "Tankstelle")}${fuelBadges(s.flags)} ${priceTag(s)}`, `${isDetour(s) ? `Abstecher ca. ${fmt(s.flags.detour_km, 1)} km hin und zurück ab der Ausfahrt` : `${fmt(s.off)} m abseits`} · nach ca. ${hm(s.tmin / 60)}${s.flags && s.flags.hours ? " · " + s.flags.hours : ""}`, s.lat, s.lon, null, ShopList.tourChipHtml(s, "fuel")]);
  } else if (tab === "rest") {
    items = r.pois.filter((p) => p.group === "rest").map((p) => [p.km, esc(p.name || p.label), `${p.label} · ${fmt(p.off)} m abseits`, p.lat, p.lon, null, ShopList.tourChipHtml(p, "rest")]);
  }
  // chip = „➕ In die Tour“ (shops.js), dann drei Spalten wie in „Einkaufen“
  $("list").innerHTML = items.length
    ? items.map(([km, t, s, la, lo, tip, chip]) => `<li data-ll="${la},${lo}"${tip ? ` title="${esc(tip)}"` : ""}${chip ? ' class="shoprow"' : ""}><span class="km">km ${fmt(km, 1)}</span><span>${t}<small>${esc(s)}</small></span>${chip || ""}</li>`).join("")
    : `<li class="empty">${tab === "issues" ? "Keine Engstellen oder Hinweise – sieht gut aus." : "Nichts gefunden."}</li>`;
}

/* ---------------------------------------------------------------- Hoehenprofil + Tankfuellung */
let lastProfile = null, resizeT = 0;
// einklappbar: gemerkte Wahl, sonst offen ab 850 px Fensterhoehe – zu = Karte bekommt die Hoehe
const profStore = (() => { try { return window.localStorage; } catch { return null; } })();
let profOpen = LayoutUI.profileOpen(profStore, "rig.profile", window.innerHeight, window.innerWidth);
function applyFold() {
  const b = $("profile-card").querySelector(".fold");
  b.setAttribute("aria-expanded", String(profOpen));
  b.querySelector(".fold-ico").textContent = profOpen ? "▾" : "▸";
  $("profile").hidden = !profOpen;
  requestAnimationFrame(() => map.invalidateSize());
}
$("profile-card").querySelector(".fold").addEventListener("click", () => {
  profOpen = !profOpen;
  try { if (profStore) profStore.setItem("rig.profile", profOpen ? "1" : "0"); } catch { /* privat */ }
  applyFold();
  if (profOpen && lastProfile) drawProfile(...lastProfile);
});
window.addEventListener("resize", () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(() => { if (lastProfile && !$("profile-card").hidden) drawProfile(...lastProfile); map.invalidateSize(); }, 200);
});

function drawProfile(r, plan) {
  const rig = r.rig || {};
  const prof = rig.profile || [];
  $("profile-card").hidden = false;
  lastProfile = [r, plan];
  applyFold();   // zugeklappt: nur die Kopfzeile, nichts zeichnen
  if (!profOpen) return;
  requestAnimationFrame(() => map.invalidateSize());   // Karte ist dadurch kleiner geworden
  // Hoehe passend zum Fenster, damit Karte + Profil zusammen auf den Schirm passen
  const cw = $("profile").clientWidth || 1000;
  const want = Math.min(260, Math.max(140, window.innerHeight * 0.24));
  const W = 1000, H = Math.max(120, Math.round((W * want) / cw)), L0 = 46, R0 = 46, T0 = 10, B0 = 28;
  lastProfile = [r, plan];
  const km = r.stats.km;
  const x = (k) => L0 + (k / km) * (W - L0 - R0);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Höhenprofil und Tankfüllung">`;
  if (prof.length > 1) {
    const emin = Math.min(...prof.map((p) => p[1])), emax = Math.max(...prof.map((p) => p[1]));
    const lo = Math.floor((emin - 20) / 50) * 50, hi = Math.ceil((emax + 20) / 50) * 50;
    const y = (e) => T0 + (1 - (e - lo) / Math.max(hi - lo, 1)) * (H - T0 - B0);
    for (let k = 1; k < prof.length; k++) {
      const [k0, e0, g0] = prof[k - 1], [k1, e1, g1] = prof[k];
      const g = Math.abs(((g0 ?? 0) + (g1 ?? 0)) / 2);
      const c = g >= 12 ? cssVar("--err") : g >= 8 ? cssVar("--warn") : g >= 5 ? "#c9b84a" : cssVar("--accent");
      s += `<path d="M${x(k0).toFixed(1)},${H - B0} L${x(k0).toFixed(1)},${y(e0).toFixed(1)} L${x(k1).toFixed(1)},${y(e1).toFixed(1)} L${x(k1).toFixed(1)},${H - B0} Z" fill="${c}" opacity=".55"/>`;
    }
    s += `<polyline fill="none" stroke="${cssVar("--ink")}" stroke-width="1.2" points="${prof.map(([k, e]) => `${x(k).toFixed(1)},${y(e).toFixed(1)}`).join(" ")}"/>`;
    for (const e of [lo, Math.round((lo + hi) / 2), hi]) s += `<text x="${L0 - 6}" y="${y(e) + 4}" text-anchor="end">${e} m</text>`;
  } else {
    s += `<text x="${W / 2}" y="${H / 2}" text-anchor="middle">Kein Höhenprofil</text>`;
  }
  // Tankfuellung (rechte Achse)
  const yf = (l) => T0 + (1 - l / veh.tank) * (H - T0 - B0);
  const pts = plan.diesel.levels.map(([k, l]) => `${x(Math.min(k, km)).toFixed(1)},${yf(Math.max(l, 0)).toFixed(1)}`).join(" ");
  s += `<line x1="${L0}" x2="${W - R0}" y1="${yf(veh.reserve)}" y2="${yf(veh.reserve)}" stroke="${cssVar("--err")}" stroke-dasharray="4 4" opacity=".6"/>`;
  s += `<polyline fill="none" stroke="#2f6fbf" stroke-width="2.5" points="${pts}"/>`;
  s += `<text x="${W - R0 + 6}" y="${yf(veh.tank) + 4}">${veh.tank} l</text><text x="${W - R0 + 6}" y="${yf(veh.reserve) + 4}" fill="${cssVar("--err")}">${veh.reserve} l</text>`;
  for (const st of plan.diesel.stops) s += `<text x="${x(st.km)}" y="${T0 + 12}" text-anchor="middle">⛽</text>`;
  for (const [k0, , n] of r.stageBounds || []) {
    if (k0 > 0) s += `<line x1="${x(k0)}" x2="${x(k0)}" y1="${T0}" y2="${H - B0}" stroke="${cssVar("--muted")}" stroke-dasharray="2 4"/>`;
    s += `<text x="${x(k0) + 3}" y="${H - B0 - 4}">E${n}</text>`;
  }
  // hoechstens ~12 Beschriftungen, auch bei Reisen ueber 4000 km
  const stepKm = [5, 10, 20, 50, 100, 200, 250, 500, 1000].find((st) => km / st <= 12) || 1000;
  for (let k = 0; k <= km; k += stepKm) s += `<text x="${x(k)}" y="${H - 8}" text-anchor="middle">${k}</text>`;
  s += `<line id="pf-cur" x1="-10" x2="-10" y1="${T0}" y2="${H - B0}" stroke="${cssVar("--ink")}"/>`;
  s += `<rect id="pf-hit" x="${L0}" y="0" width="${W - L0 - R0}" height="${H}" fill="transparent"/></svg>`;
  s += `<p class="hint">Fläche: Höhe, gefärbt nach Steigung · blaue Linie: Dieselstand mit Tankstopps · rot gestrichelt: Reserve.</p>`;
  $("profile").innerHTML = s;
  const svg = $("profile").querySelector("svg");
  $("pf-hit").addEventListener("mousemove", (ev) => {
    const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    const k = Math.max(0, Math.min(km, ((p.x - L0) / (W - L0 - R0)) * km));
    $("pf-cur").setAttribute("x1", x(k)); $("pf-cur").setAttribute("x2", x(k));
    moveCursor(llAtKm(r, k));
  });
}

/* ---------------------------------------------------------------- Upload + Jobs */
async function submit(promise) {
  $("error").hidden = true; $("missing").hidden = true; $("progress").hidden = false;
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
/** Fertige Analyse anzeigen – frisch gerechnet oder gespeichert zur Tour (window.showAnalysisResult). */
function showResult(result) {
  $("error").hidden = true;
  showingImport = !window.plannerResult;   // Planer-Route -> Import bleibt als Geisterstrecke stehen
  if (window.plannerResult) $("route-toggle").checked = true;   // neue eigene Route ist erst mal sichtbar
  showMissing(result.missing);
  if (result.trip) {
    trip = result; combined = combine(trip); stageIdx = 0; route = combined;
    $("opt-stage").hidden = false; $("layer").value = "stage";
  } else {
    trip = null; combined = null; route = result;
    $("opt-stage").hidden = true; if ($("layer").value === "stage") $("layer").value = "fit";
  }
  $("vehicle-card").open = false;
  $("cov-toggle").checked = false; map.removeLayer(covLayer);
  renderAll();
  LayoutUI.fitWhenSettled(map, L.latLngBounds(route.segments.flatMap((s) => s.c)));
  drawGhost();
}
window.showAnalysisResult = showResult;

/* ---------------------------------------------------------------- Import als Geisterstrecke
   Eine hochgeladene GPX (oder ein Beispiel) bleibt nach dem Planen als gestrichelte Referenz auf der Karte; der
   Planer uebernimmt auf Wunsch ihre Eckdaten (static-rig/importplan.js). „Alle löschen“ im Planer zeigt wieder die
   Analyse des Imports. Verglichen wird im Ergebnis: Länge, Fahrzeit, bergauf, Engstellen – alles mit eurem Fahrzeug. */
map.createPane("ghost").style.zIndex = 390;   // unter der farbigen Route (overlayPane = 400)
const ghostLayer = L.layerGroup();
// an/aus merkt sich der Ebenen-Knopf (rig.layers, Name "ghost-toggle"); neu = an, s. Ergaenzung unten bei mountLayerButton
$("ghost-toggle").addEventListener("change", () => drawGhost());
$("route-toggle").addEventListener("change", () => drawGhost());
// Wegpunkte aus der GPX-Datei (Namen + Zwischenziele); lange <rte> sind eher die Strecke selbst
function gpxWpts(text) {
  if (!text) return [];
  try {
    const doc = new DOMParser().parseFromString(text, "application/xml");
    const take = (tag) => [...doc.getElementsByTagName(tag)].map((e) => ({ lat: +e.getAttribute("lat"), lon: +e.getAttribute("lon"),
      name: ((e.getElementsByTagName("name")[0] || {}).textContent || "").trim() }));
    const rte = take("rtept");
    return [...take("wpt"), ...(rte.length <= 60 ? rte : [])];
  } catch { return []; }
}
function readImport(f) {
  importText = null;
  if (f && f.size < 6e6 && f.text) f.text().then((t) => { importText = t; }).catch(() => {});
}
function setImport(result) {
  const t = ImportPlan.trackFrom(result);
  importSnap = t.track.length > 1 ? { result, ...t, wpts: gpxWpts(importText), name: result.name || "" } : null;
  importText = null;
  drawGhost();
  document.dispatchEvent(new CustomEvent("import-changed"));
}
function drawGhost() {
  ghostLayer.clearLayers();
  const show = !!importSnap && !showingImport;
  $("ghost-row").hidden = !show;
  if (show) L.polyline(importSnap.track, { pane: "ghost", color: cssVar("--muted") || "#868e96", weight: 5, opacity: 0.7,
    dashArray: "2 9", lineCap: "round", interactive: false }).addTo(ghostLayer);
  if (show && $("ghost-toggle").checked) ghostLayer.addTo(map); else map.removeLayer(ghostLayer);
  // liegen eigene Route und Import nebeneinander, ist auch die eigene ueber „Ebenen“ ausblendbar
  // (nicht gemerkt – beim Laden und bei jeder neuen Route wieder an); sonst ist sie immer sichtbar
  $("route-row").hidden = !show;
  if (!show || $("route-toggle").checked) { if (!map.hasLayer(routeLayer)) routeLayer.addTo(map); } else map.removeLayer(routeLayer);
}
function sumUp(res) {
  const r = res.trip ? combine(res) : res;
  const is = evaluate(r).filter((i) => i.kind !== "lez");
  return { km: r.stats.km, h: travelHours(r), up: r.rig && r.rig.ascent != null ? r.rig.ascent : null, stages: res.trip ? res.stages.length : 1,
    red: is.filter((i) => i.sev === "red").length, amber: is.filter((i) => i.sev === "amber").length };
}
function renderImportCompare(cur) {
  const box = $("cmp-import");
  if (!importSnap || showingImport || !cur) { box.hidden = true; box.innerHTML = ""; return; }
  const a = sumUp(importSnap.result), b = sumUp(cur);
  // besser/schlechter nur bei klaren Unterschieden (Engstellen immer)
  const mark = (x, y, tol) => (x == null || y == null || Math.abs(y - x) <= tol ? "" : y < x ? "better" : "worse");
  const rows = [
    ["Länge", `${fmt(a.km, 0)} km`, `${fmt(b.km, 0)} km`, mark(a.km, b.km, Math.max(2, a.km * 0.02))],
    ["Fahrzeit", hm(a.h), hm(b.h), mark(a.h, b.h, 0.1)],
    ...(a.stages > 1 || b.stages > 1 ? [["Etappen", String(a.stages), String(b.stages), ""]] : []),
    ["bergauf", a.up == null ? "–" : `${fmt(a.up)} hm`, b.up == null ? "–" : `${fmt(b.up)} hm`, mark(a.up, b.up, 50)],
    ["Stellen passen nicht", String(a.red), String(b.red), mark(a.red, b.red, 0)],
    ["knapp / prüfen", String(a.amber), String(b.amber), mark(a.amber, b.amber, 0)],
  ];
  box.innerHTML = `<h4><i class="ghost-sw"></i>Vergleich mit der importierten Tour${importSnap.name ? ` „${esc(importSnap.name)}“` : ""}</h4>
    <table class="cmp-tab"><thead><tr><th></th><th>importiert</th><th>neu geplant</th></tr></thead><tbody>${
      rows.map(([k, x, y, c]) => `<tr><th>${esc(k)}</th><td>${esc(x)}</td><td class="${c}">${esc(y)}</td></tr>`).join("")}</tbody></table>
    <p class="hint">Beides mit eurem aktuellen Fahrzeugprofil bewertet. Die importierte Tour seht ihr gestrichelt auf der Karte.
      <button type="button" class="linkbtn" id="cmp-back">Importierte Tour wieder anzeigen</button></p>`;
  box.hidden = false;
}
function restoreImport() {
  if (!importSnap) return false;
  window.plannerResult = false;
  showResult(importSnap.result);
  return true;
}
$("cmp-import").addEventListener("click", (e) => { if (e.target.closest("#cmp-back")) restoreImport(); });
// ghost(): liegt die Geisterstrecke gerade auf der Karte? (Karte zeichnet per Canvas – fuer die e2e-Tests)
window.importRoute = { snap: () => importSnap, showing: () => showingImport, restore: restoreImport,
  ghost: () => map.hasLayer(ghostLayer) && ghostLayer.getLayers().length > 0,
  planned: () => map.hasLayer(routeLayer) && routeLayer.getLayers().length > 0 };

async function poll(id) {
  const t0 = Date.now();
  let down = 0;
  for (;;) {
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
    if (j.missing && $("missing").hidden) showMissing(j.missing);
    if (j.state === "error") {
      showMissing(j.missing);
      if (j.missing && j.missing.length && /Kartendaten gerade nicht abrufbar/.test(j.msg))
        throw new Error("Die Live-Kartendaten von OpenStreetMap sind gerade nicht erreichbar. Später nochmal versuchen – oder oben die Region aufnehmen.");
      throw new Error(j.msg);
    }
    if (j.state === "done") {
      setProgress(100, "fertig"); setTimeout(() => { $("progress").hidden = true; }, 600);
      showResult(j.result);
      if (!window.plannerResult) setImport(j.result);   // Referenz fuer Geisterstrecke + Eckdaten
      // der Planer legt die Analyse zur gespeicherten Tour (Meine Touren)
      document.dispatchEvent(new CustomEvent("analysis-done", { detail: j.result }));
      if (window.plannerResult && window.planner && window.planner.lezAnalyzed) window.planner.lezAnalyzed(lezAvoidIds());
      return;
    }
    setProgress(j.progress, QueueText.label(j));
  }
}
/* Fortschritt: nie rueckwaerts – der Server meldet je Arbeitsschritt, das springt. Angezeigt wird der
   hoechste Stand; dazwischen kriecht das Wohnmobil langsam weiter (hoechstens 6 % ueber den letzten echten Stand). */
let progShown = 0, progTarget = 0, progTimer = 0;
function setProgress(p, msg) {
  p = Math.max(0, Math.min(100, +p || 0));
  if (p <= 1) progShown = progTarget = 1;
  progTarget = Math.max(progTarget, p);
  $("progress-msg").textContent = msg;
  $("progress").classList.toggle("gespann", veh.trailer === true);   // nur mit Anhaenger im aktiven Profil ein Gespann
  $("progress").title = `Fahrzeugprofil: ${store.active}${veh.trailer === true ? " (mit Anhänger)" : ""}`;
  if (!progTimer) progTimer = setInterval(progTick, 300);
  progTick();
}
function progTick() {
  const cap = progTarget >= 100 ? 100 : Math.min(progTarget + 6, 97);
  if (progShown < progTarget) progShown += Math.max(0.5, (progTarget - progShown) * 0.2);
  else if (progShown < cap) progShown += Math.max(0.03, (cap - progShown) * 0.015);
  progShown = Math.min(progShown, cap);
  // Fahrzeug haengt vorne am Balken: Startpunkt so weit rechts, dass es ganz auf dem Balken steht
  const car = $("progress").classList.contains("gespann") ? 129 : 77;   // Breite − Ueberstand vorne (rig.css)
  $("bar").style.width = `calc(${car}px + (100% - ${car}px) * ${(Math.max(0, progShown) / 100).toFixed(4)})`;
  if (progShown >= 100 || $("progress").hidden) { clearInterval(progTimer); progTimer = 0; }
}

const clock = (e) => new Date(e * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
function showMissing(list) {
  const box = $("missing");
  list = (list || []).filter((m) => m.share > 0);
  if (!list.length) { box.hidden = true; return; }
  box.innerHTML = `<p>Ein Teil der Route liegt außerhalb der lokalen Kartendaten – Live-Daten sind langsam und unter Last unzuverlässig:</p>` + list.map((m) => {
    const meta = [m.share ? `${m.share} % der Route` : "", m.size_mb ? `${m.size_mb} MB` : "", m.est_min ? `Aufbau ca. ${m.est_min} Min.` : ""].filter(Boolean).join(" · ");
    const act = !m.region ? "" : m.queued ? `<span class="ok">eingeplant – fertig ca. ${clock(m.eta)} Uhr</span>` : `<button type="button" data-region="${esc(m.region)}">Region aufnehmen</button>`;
    return `<div class="reg"><b>${esc(m.name)}</b><small>${esc(meta)}</small>${act}</div>`;
  }).join("");
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
    const span = document.createElement("span"); span.className = "ok";
    span.textContent = d.eta ? `eingeplant – fertig ca. ${clock(d.eta)} Uhr, dann nochmal analysieren` : d.msg;
    b.replaceWith(span);
  } catch (err) { b.disabled = false; b.textContent = "Region aufnehmen"; alert(err.message); }
});

function uploadFile(f) { if (!f) return; const fd = new FormData(); fd.append("file", f, f.name); submit(fetch("/api/analyze", { method: "POST", body: fd })); }
$("file").addEventListener("change", (e) => { window.plannerResult = false; readImport(e.target.files[0]); uploadFile(e.target.files[0]); e.target.value = ""; });
const drop = $("drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => { window.plannerResult = false; readImport(e.dataTransfer.files[0]); uploadFile(e.dataTransfer.files[0]); });
document.querySelectorAll("[data-demo]").forEach((b) => b.addEventListener("click", () => { window.plannerResult = false; importText = null; submit(fetch(`/api/demo/${b.dataset.demo}`, { method: "POST" })); }));

const jump = (e) => {
  const el = e.target.closest("[data-ll]");
  if (!el) return;
  const [la, lo] = el.dataset.ll.split(",").map(Number);
  map.setView([la, lo], Prefs.zoom().jump); moveCursor([la, lo]);
  if (window.matchMedia("(max-width: 900px)").matches) $("map").scrollIntoView({ behavior: "smooth" });
};
["verdict", "fuelplan", "steep", "list"].forEach((id) => $(id).addEventListener("click", jump));
// Drueberfahren zeigt die Stelle wie beim Hoehenprofil – Punkt auf der Strecke, ohne Zoom; liegt sie
// ausserhalb des Ausschnitts, schiebt sich die Karte nur hin (Zoom bleibt)
const peek = (e) => {
  const el = e.target.closest?.("[data-ll]");
  if (!el) return;
  const ll = el.dataset.ll.split(",").map(Number);
  if (ll.length !== 2 || ll.some((v) => !Number.isFinite(v))) return;
  moveCursor(ll);
  if (!map.getBounds().contains(ll)) map.panTo(ll);
};
["verdict", "fuelplan", "steep", "list"].forEach((id) => $(id).addEventListener("mouseover", peek));
// fuer die Abnahmetests (e2e): Karte und Punkt-Position lesbar
window.__rigMap = map;
window.__rigCursor = () => (cursor ? cursor.getLatLng() : null);
$("verdict").addEventListener("click", (e) => {
  const b = e.target.closest("#lez-avoid");
  if (!b || !window.planner || !window.planner.avoidZones) return;
  b.disabled = true; b.textContent = "plane neu …";
  if (!window.planner.avoidZones(lezAvoidIds())) { b.disabled = false; b.textContent = "umfahren"; }
});
// Naechte mit −/+: 0..30 wie bisher; Pfeiltasten auf den Knoepfen
function stepNights(btn, d) {
  const i = Number(btn.dataset.nightsStep), cfg = tripCfg();
  const cur = cfg.nights[i] ?? schedule().rows[i].nights;
  cfg.nights[i] = LayoutUI.nightsStep(cur, d ?? Number(btn.dataset.d));
  saveTripCfg(cfg); renderAll();
  const again = document.querySelector(`#trip [data-nights-step="${i}"][data-d="${btn.dataset.d}"]`);
  if (again) again.focus();
}
$("trip").addEventListener("keydown", (e) => {
  const st = e.target.closest("[data-nights-step]");
  if (st && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); stepNights(st, e.key === "ArrowUp" ? 1 : -1); }
});
$("trip").addEventListener("click", (e) => {
  if (e.target.closest("input")) return;
  const st = e.target.closest("[data-nights-step]");
  if (st) { stepNights(st); return; }
  const b = e.target.closest("[data-stage]");
  if (b) selectStage(Number(b.dataset.stage));
});
$("trip").addEventListener("change", (e) => {
  const cfg = tripCfg();
  if (e.target.id === "t-date") cfg.date = e.target.value;
  else if (e.target.id === "t-time") cfg.time = e.target.value || "08:30";
  else if (e.target.id === "t-pause") {
    cfg.pause = Aufenthalt.PAUSE[e.target.value] ? e.target.value : "auto";
    if (window.planner && window.planner.setPause) window.planner.setPause(cfg.pause);
  }
  else if (e.target.dataset.nights !== undefined) {
    const v = parseInt(e.target.value, 10);
    if (Number.isFinite(v) && v >= 0 && v <= 30) cfg.nights[e.target.dataset.nights] = v;
  } else return;
  saveTripCfg(cfg); renderAll();
});
$("tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-tab]"); if (!b) return; tab = b.dataset.tab; saveUi(); if (route) renderAll(); });
$("layer").addEventListener("change", () => { if (route) renderAll(); else legend(); });
$("toggles").addEventListener("change", () => { syncToggles(); saveUi(); });
const shownLayers = () => [...document.querySelectorAll("#toggles input[data-m]")].filter((cb) => cb.checked).map((cb) => cb.dataset.m);
function saveUi() {
  if (Prefs.synced) Prefs.put("ui", { tab, layers: shownLayers(), tripH: tripSplit.frac(), layout: Dock.layout(), colW: colSplit.frac() });
}

/* Schieber links ↔ Karte: Breite der linken Spalte als Anteil der Fensterbreite, gemerkt am Login + im Browser */
const colSplit = (() => {
  const bar = $("col-split"), grid = document.querySelector(".layout");
  let frac = null;
  try { frac = parseFloat(localStorage.getItem("rig.colW")) || null; } catch { /* privat */ }
  const clamp = (px) => Math.max(320, Math.min(px, window.innerWidth - 460));
  function apply() {
    if (frac) grid.style.setProperty("--left-w", `${Math.round(clamp(frac * window.innerWidth))}px`);
    else grid.style.removeProperty("--left-w");
    requestAnimationFrame(() => { map.invalidateSize(); window.dispatchEvent(new Event("colresize")); });
  }
  function set(px, store = true) {
    frac = px == null ? null : clamp(px) / window.innerWidth;
    apply();
    if (!store) return;
    try { if (frac) localStorage.setItem("rig.colW", frac.toFixed(3)); else localStorage.removeItem("rig.colW"); } catch { /* privat */ }
    saveUi();
  }
  const leftW = () => document.querySelector(".panel").getBoundingClientRect().width;
  bar.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    bar.setPointerCapture(e.pointerId); bar.classList.add("drag");
    const x0 = e.clientX, w0 = leftW();
    const move = (ev) => set(w0 + ev.clientX - x0, false);
    const up = () => { bar.classList.remove("drag"); bar.removeEventListener("pointermove", move); set(leftW()); window.dispatchEvent(new Event("resize")); };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up, { once: true });
    bar.addEventListener("pointercancel", up, { once: true });
  });
  bar.addEventListener("dblclick", () => { set(null); window.dispatchEvent(new Event("resize")); });
  bar.addEventListener("keydown", (e) => {
    const d = e.key === "ArrowRight" ? 40 : e.key === "ArrowLeft" ? -40 : 0;
    if (d) { e.preventDefault(); set(leftW() + d); window.dispatchEvent(new Event("resize")); }
  });
  window.addEventListener("resize", () => { if (frac) apply(); });
  apply();
  return { frac: () => (frac ? Math.round(frac * 1000) / 1000 : null), load(f) { if (typeof f === "number" && f > 0.15 && f < 0.85) { frac = f; apply(); } } };
})();

/* Ergebnis-Karten als Dashboard: jede Karte steckt in einem Rahmen mit ⠿ (ziehen), ▾ (einklappen),
   ⇄ (andere Seite), ↑/↓. Zwei Docks: links unter dem Kopf, rechts unter der Karte. Anordnung am Login + im Browser.
   Logik in /static/dock.js (gemeinsam mit Ride); Stand am Login in prefs „ui.layout“. */
const Dock = DockKit.mount({ left: "dock-left", right: "dock-right", def: DockKit.DEFAULTS.rig, key: "rig.layout",
  result: "result", reset: "dock-reset", map: () => map, onChange: () => saveUi() });

/* Schieber Karte ↕ rechtes Dock: Hoehe als Anteil der Fensterhoehe, gemerkt am Login + im Browser */
const tripSplit = (() => {
  const bar = $("trip-split"), box = $("dock-right");
  let frac = null;
  try { frac = parseFloat(localStorage.getItem("rig.tripH")) || null; } catch { /* privat */ }
  const clamp = (px) => Math.max(110, Math.min(px, $("map").parentElement.clientHeight - 300));
  function apply() {
    box.style.height = frac ? `${Math.round(clamp(frac * window.innerHeight))}px` : "";
    map.invalidateSize();
  }
  function set(px, store = true) {
    frac = px == null ? null : clamp(px) / window.innerHeight;
    apply();
    if (!store) return;
    try { if (frac) localStorage.setItem("rig.tripH", frac.toFixed(3)); else localStorage.removeItem("rig.tripH"); } catch { /* privat */ }
    saveUi();
  }
  // nur zeigen, wenn unter der Karte etwas steht
  new MutationObserver(() => { bar.hidden = box.hidden; if (!box.hidden) apply(); }).observe(box, { attributes: true, attributeFilter: ["hidden"] });
  bar.hidden = box.hidden;
  bar.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    bar.setPointerCapture(e.pointerId); bar.classList.add("drag");
    const bottom = box.getBoundingClientRect().bottom;
    const move = (ev) => set(bottom - ev.clientY - 6, false);
    const up = () => { bar.classList.remove("drag"); bar.removeEventListener("pointermove", move); set(box.offsetHeight); };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up, { once: true });
    bar.addEventListener("pointercancel", up, { once: true });
  });
  bar.addEventListener("dblclick", () => set(null));
  bar.addEventListener("keydown", (e) => {
    const d = e.key === "ArrowUp" ? 30 : e.key === "ArrowDown" ? -30 : 0;
    if (d) { e.preventDefault(); set(box.offsetHeight + d); }
  });
  window.addEventListener("resize", () => { if (!box.hidden) apply(); });
  return { frac: () => (frac ? Math.round(frac * 1000) / 1000 : null), load(f) { if (typeof f === "number" && f > 0.05 && f < 0.9) { frac = f; if (!box.hidden) apply(); } } };
})();
// Ebene „Umweltzonen“ ist neu und standardmaessig an – gemerkte Ebenen von vorher einmalig ergaenzen
try {
  const raw = localStorage.getItem("rig.layers");
  if (raw && !localStorage.getItem("rig.layers.lez")) {
    const a = JSON.parse(raw);
    if (Array.isArray(a) && !a.includes("lez")) localStorage.setItem("rig.layers", JSON.stringify([...a, "lez"]));
  }
  localStorage.setItem("rig.layers.lez", "1");
  // Ebene „Importierte Strecke“ ebenso – sonst stuende sie bei gemerkten Ebenen von vorher auf aus
  const raw2 = localStorage.getItem("rig.layers");
  if (raw2 && !localStorage.getItem("rig.layers.ghost")) {
    const a = JSON.parse(raw2);
    if (Array.isArray(a) && !a.includes("ghost-toggle")) localStorage.setItem("rig.layers", JSON.stringify([...a, "ghost-toggle"]));
  }
  localStorage.setItem("rig.layers.ghost", "1");
} catch { /* privat */ }
LayoutUI.mountLayerButton(map, $("toggles"), "rig.layers", syncToggles);
$("route-toggle").checked = true; drawGhost();   // „Geplante Route“ startet immer sichtbar (s. drawGhost)

/* ---------------------------------------------------------------- Hilfe */
$("help-btn").addEventListener("click", () => $("help").showModal());
$("help").addEventListener("click", (e) => { if (e.target === $("help")) $("help").close(); });
try {
  if (!localStorage.getItem("rig.helpSeen") && !new URLSearchParams(location.search).get("demo")) {
    $("help").showModal(); localStorage.setItem("rig.helpSeen", "1");
  }
} catch { /* privat */ }

/* ---------------------------------------------------------------- Theme + Info */
function syncThemeButton() {
  const dark = document.documentElement.getAttribute("data-theme") !== "light";
  const b = $("theme-toggle");
  b.querySelector(".ico").textContent = dark ? "☀️" : "🌙";
  b.querySelector(".lbl").textContent = dark ? "Hell" : "Dunkel";
}
$("theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("ra.theme", next); } catch { /* privat */ }
  syncThemeButton(); if (route) renderAll(); else legend();
});
syncThemeButton();

// Kartendaten: auf der Startseite nur eine Zeile, Laenderliste und Details unter Funktionen
function coverageHint(i, intro) {
  const cs = (i.osm && i.osm.countries) || [];
  if (!cs.length) return esc(intro) + `Kartendaten kommen live von OpenStreetMap (dauert etwas). <a href="/features.html#karten">Mehr</a>`;
  return esc(intro) + `Lokale Kartendaten für ${cs.length} Länder – <a href="/features.html#karten">welche?</a>`;
}

fetch("/api/info").then((r) => r.json()).then((i) => {
  const names = (i.osm && i.osm.names) || {};
  const have = i.osm ? i.osm.regions.map((r) => names[r] || r.split("/").pop()) : [];
  $("ver").textContent = `RigAnalyzer v${i.version} · Stand ${i.stamp}`;
  if (i.lez) { lezMeta = i.lez; if (route) renderAll(); }
  const nc = ((i.osm && i.osm.countries) || []).length;
  $("osm-state").textContent = have.length ? `lokale Daten: ${nc} Länder, ${have.length} Regionen` : "nur Live-Kartendaten";
  $("limits").innerHTML = coverageHint(i, `Bis ${i.max_km} km, max. ${i.max_mb} MB. `);
}).catch(() => {});

bindVehicle();
// „€ Preis holen“ in der Liste „Alle Tankstellen“: nur diese eine Station abfragen (capture: vor dem Karten-Sprung)
$("list").addEventListener("click", (e) => {
  const b = e.target.closest(".price-btn");
  if (!b) return;
  e.stopPropagation();
  const base = combined || route, osm = b.dataset.osm, [la, lo] = b.dataset.ll.split(",").map(Number);
  b.disabled = true; b.textContent = "hole …";
  fetch("/api/fuel/prices", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stations: [[osm, la, lo]] }) })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (liveFor !== base) return;
      liveAsked.add(osm);
      if (j && j.prices && j.prices[osm]) { Object.assign(livePrices, j.prices); Object.assign(liveSources, j.sources || {}); liveSummary(); }
      else liveNone.add(osm);
      renderAll();
    }).catch(() => { b.disabled = false; b.textContent = "€ Preis holen"; });
}, true);
$("fuelplan").addEventListener("click", (e) => {
  if (e.target.id !== "fuel-refresh" || !livePlan) return;
  e.stopPropagation();
  ensurePrices(livePlan, true);
  renderAll();   // Knopf zeigt „prüfe …“, Ergebnis rechnet danach neu
});
// „ändern“ (Maut-Karte u. a.): Fahrzeugprofil aufklappen und zum Schalter springen – die einzige Stelle dafuer
document.addEventListener("click", (e) => {
  const b = e.target.closest && e.target.closest("[data-veh-edit]");
  if (!b) return;
  const card = $("vehicle-card"), el = $(b.dataset.vehEdit);
  if (card) card.open = true;
  if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); setTimeout(() => el.focus(), 350);
    const l = el.closest("label"); if (l) { l.classList.add("flash"); setTimeout(() => l.classList.remove("flash"), 1600); } }
});
legend();
if ($("cov-toggle").checked) covLayer.addTo(map);
loadCoverage(!new URLSearchParams(location.search).get("demo")).then(() => {
  if (!route && $("cov-toggle").checked) $("legend").innerHTML += AGE_LEGEND;
});
const demoParam = new URLSearchParams(location.search).get("demo");
if (demoParam && /^[a-z0-9]+$/i.test(demoParam)) submit(fetch(`/api/demo/${demoParam}`, { method: "POST" }));

// „mehr unter ⛽ Sprit-Info“ im Tankplan springt in den Reiter
document.addEventListener("click", (e) => {
  const b = e.target.closest && e.target.closest("[data-goto-tab]");
  if (!b) return;
  tab = b.dataset.gotoTab;
  if (route) renderAll();
  const t = document.querySelector(`#tabs [data-tab="${tab}"]`);
  if (t) t.scrollIntoView({ behavior: "smooth", block: "center" });
});

/* ---------------------------------------------------------------- Menü „⚙ Karte“ */
// Zoom, Klick-Zoom und „Ergebnis-Kacheln zurücksetzen“ – auf/zu per Knopf, schließt bei Klick daneben und Escape
(() => {
  const btn = $("map-menu-btn"), panel = $("map-menu-panel"), box = $("map-menu");
  if (!btn || !panel) return;
  const set = (open) => { panel.hidden = !open; btn.setAttribute("aria-expanded", String(open)); };
  btn.addEventListener("click", () => {
    set(panel.hidden);
    if (!panel.hidden) panel.querySelector("select").focus();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) { set(false); btn.focus(); }
  });
  document.addEventListener("click", (e) => { if (!box.contains(e.target)) set(false); });
})();

/* ---------------------------------------------------------------- Vorlieben am Login */
function applyZoom() {
  const z = Prefs.zoom();
  map.options.wheelPxPerZoomLevel = Prefs.WHEEL[z.wheel] || 12;   // Leaflet liest das bei jedem Dreh neu
  if ($("pref-wheel")) $("pref-wheel").value = z.wheel;
  if ($("pref-jump")) $("pref-jump").value = String(z.jump);
}
applyZoom();
["pref-wheel", "pref-jump"].forEach((id) => $(id) && $(id).addEventListener("change", () => {
  Prefs.put("zoom", { wheel: $("pref-wheel").value, jump: parseInt($("pref-jump").value, 10) || 16 });
  applyZoom();
}));
fetch("/api/me/prefs").then((r) => (r.ok ? r.json() : null)).then((d) => {
  if (!d) return;
  Prefs.data = d;
  // Fahrzeugprofile: neuerer Stand gewinnt (Browser ohne Zeitstempel = aelter als alles vom Server)
  const sp = d.profiles;
  if (sp && sp.profiles && sp.profiles[sp.active] && sp.at > (store.at || 0)) {
    store.profiles = {};
    for (const [n, p] of Object.entries(sp.profiles)) store.profiles[n] = { ...DEFAULTS, ...p };
    store.active = sp.active; veh = store.profiles[store.active];
    saveStore(false, false, sp.at);
    fillInputs(); pushLezSettings(); if (route) renderAll();
  }
  if (d.ui) {
    if (d.ui.tab) tab = d.ui.tab;
    if (d.ui.tripH) tripSplit.load(d.ui.tripH);
    if (d.ui.colW) colSplit.load(d.ui.colW);
    if (d.ui.layout && d.ui.layout.left) { Dock.apply(d.ui.layout); try { localStorage.setItem("rig.layout", JSON.stringify(d.ui.layout)); } catch { /* privat */ } }
    if (Array.isArray(d.ui.layers)) {
      document.querySelectorAll("#toggles input[data-m]").forEach((cb) => { cb.checked = d.ui.layers.includes(cb.dataset.m); });
      // „Importierte Strecke“ ist nur im Browser gemerkt (kein data-m) – beim Uebernehmen vom Login nicht verlieren
      const keep = $("ghost-toggle").checked ? ["ghost-toggle"] : [];
      try { LayoutUI.saveLayers(localStorage, "rig.layers", [...d.ui.layers, ...keep]); } catch { /* privat */ }
      syncToggles();
    }
    if (route) renderAll();
  }
  applyZoom();
}).catch(() => {}).finally(() => {
  Prefs.synced = true;
  // noch nichts am Login: die Profile aus diesem Browser hochladen
  if (!(Prefs.data.profiles && Prefs.data.profiles.at)) Prefs.put("profiles", { active: store.active, profiles: store.profiles });
});

