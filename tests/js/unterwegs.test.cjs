// node --test tests/js/  –  „🧭 Unterwegs entdecken“
const test = require("node:test");
const assert = require("node:assert");
globalThis.CampFilter = require("../../app/static-rig/camps-filter.js");
const W = require("../../app/static-rig/unterwegs.js");

const P = [
  { group: "sight", label: "Aussichtspunkt", name: "Point Sublime", km: 30 },
  { group: "food", label: "Café", name: "", km: 10, flags: { hours: "Mo-Su 08:00-18:00" } },
  { group: "service", label: "Gasflaschen", name: "Gas Meier", km: 20, flags: { kind: "gas", hours: "Mo-Fr 09:00-12:00" } },
  { group: "shop", label: "Discounter", name: "Lidl", km: 5 },
  { group: "fuel", label: "Tankstelle", km: 6 },
];

test("filterWay: nur Sehenswertes, Einkehr, Service; Kategorien filtern", () => {
  assert.deepStrictEqual(W.filterWay(P, {}).map((p) => p.group), ["sight", "food", "service"]);
  assert.deepStrictEqual(W.filterWay(P, { cats: ["food", "service"] }).map((p) => p.group), ["food", "service"]);
});

test("filterWay openNow: Geschlossenes fällt weg, Sehenswertes (ohne Zeiten) bleibt", () => {
  const sun = new Date(2027, 4, 30, 14, 0);   // Sonntag 14 Uhr
  assert.deepStrictEqual(W.filterWay(P, { openNow: true }, sun).map((p) => p.group), ["sight", "food"]);
});

test("icon, Name-Ersatz, Zählung", () => {
  assert.deepStrictEqual(P.slice(0, 3).map(W.icon), ["🔭", "☕", "🔥"]);
  assert.strictEqual(W.icon({ group: "sight", label: "Unbekannt" }), "⭐");
  assert.strictEqual(W.name(P[1]), "Café");
  assert.deepStrictEqual(W.counts(P), { sight: 1, food: 1, service: 1 });
  assert.deepStrictEqual([...P].filter((p) => W.CATS.includes(p.group)).sort(W.byKm).map((p) => p.km), [10, 20, 30]);
});
