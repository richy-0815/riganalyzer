// node --test tests/js/  –  Detail-Karte: reine Hilfsfunktionen
const test = require("node:test");
const assert = require("node:assert");
globalThis.CampFilter = require("../../app/static-rig/camps-filter.js");
const D = require("../../app/static-rig/detailcard.js");

test("services: Stellplatz-Merkmale als Chips", () => {
  const c = D.services("camp", { power: true, water: true, dump: true, dog: "leashed", fee: false, shower: null });
  assert.deepStrictEqual(c, ["🔌 Strom", "🚰 Wasser", "🚽 Entsorgung", "🐕 Hunde (an der Leine)", "💶 kostenlos"]);
  assert.deepStrictEqual(D.services("camp", {}), []);
});

test("services: Werkstatt-Leistungen aus service:vehicle:*, unbekannte fallen raus", () => {
  assert.deepStrictEqual(D.services("service", { services: ["motorhome", "gas", "xyz", "tyres"], gas_exchange: true }),
    ["Wohnmobile", "Gas", "Reifen", "🔥 Gasflaschen-Tausch"]);
  assert.deepStrictEqual(D.services("shop", { services: ["gas"] }), []);
});

test("Links: Telefon, Website, E-Mail nur, wenn brauchbar", () => {
  assert.strictEqual(D.telHref("+49 (0)30 123-456; 0170 1"), "tel:+49030123456");
  assert.strictEqual(D.telHref("112"), "");
  assert.strictEqual(D.webHref("https://a.de/x"), "https://a.de/x");
  assert.strictEqual(D.webHref("javascript:alert(1)"), "");
  assert.strictEqual(D.webHref("www.a.de"), "");
  assert.strictEqual(D.mailHref("info@a.de"), "mailto:info@a.de");
  assert.strictEqual(D.mailHref("kein mail"), "");
});

test("trim: lange Beschreibung an Satz-/Wortgrenze kuerzen", () => {
  assert.deepStrictEqual(D.trim("kurz"), { short: "kurz", rest: "" });
  const t = `${"Erster Satz ist lang genug hier. ".repeat(4)}Und dann kommt noch sehr viel mehr Text, der weg soll.`;
  const r = D.trim(t, 100);
  assert.ok(r.short.endsWith(".") && r.short.length <= 101 && r.rest.length > 0);
  assert.strictEqual(`${r.short} ${r.rest}`, t.trim());
});

test("commonsFile: nur Dateien (Bilder), keine Kategorien", () => {
  assert.strictEqual(D.commonsFile("File:Burg Eltz.jpg"), "Burg Eltz.jpg");
  assert.strictEqual(D.commonsFile("Category:Castellane"), "");
  assert.strictEqual(D.commonsFile("File:plan.svg"), "");
});

test("hoursLine: offen/zu mit heutigen Zeiten", () => {
  const mo10 = new Date("2026-11-23T10:00"), mo20 = new Date("2026-11-23T20:00");
  const oh = "Mo-Fr 08:30-12:30,14:00-19:00";
  assert.deepStrictEqual(D.hoursLine(oh, null, mo10), { state: "open", text: "geöffnet bis 12:30 · heute 08:30–12:30, 14:00–19:00" });
  assert.deepStrictEqual(D.hoursLine(oh, null, mo20), { state: "closed", text: "geschlossen · heute 08:30–12:30, 14:00–19:00" });
  assert.deepStrictEqual(D.hoursLine("24/7", null, mo10), { state: "open", text: "geöffnet · 24 Stunden" });
  assert.deepStrictEqual(D.hoursLine("", null, mo10), { state: "unknown", text: "" });
});

test("seasonDe: Saison deutsch", () => {
  assert.strictEqual(D.seasonDe("Mar-Nov"), "Mär–Nov");
  assert.strictEqual(D.seasonDe("May,Oct-Dec"), "Mai, Okt–Dez");
});

test("services: echte OSM-Schluessel aus dem DE-Datensatz (0.54.1), doppelte Bedeutung nur einmal", () => {
  assert.deepStrictEqual(D.services("service", { services: ["motor_home", "caravan_repair", "car_repair", "repairs", "wheels", "tyres"] }),
    ["Wohnmobile", "Wohnwagen-Reparatur", "Reparatur", "Räder/Felgen", "Reifen"]);
});
