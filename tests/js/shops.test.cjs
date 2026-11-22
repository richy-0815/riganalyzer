// node --test tests/js/  –  Filter und Texte fuer „Einkaufen“ (RigAnalyzer)
const test = require("node:test");
const assert = require("node:assert");
globalThis.CampFilter = require("../../app/static-rig/camps-filter.js");
const S = require("../../app/static-rig/shops.js");

const shop = (kind, hours, km = 1) => ({ group: "shop", km, label: S.shopKindLabel(kind), flags: { kind, hours } });
const at = (iso) => new Date(iso);
const pois = [shop("discount", "Mo-Sa 07:00-20:00"), shop("bakery", "Mo-Sa 06:00-12:00"), shop("farm", null), { group: "camp", flags: {} }];

test("filterShops: nur Laeden, nach Arten", () => {
  assert.strictEqual(S.filterShops(pois, {}, at("2026-07-06T15:00")).length, 3);
  assert.strictEqual(S.filterShops(pois, { kinds: ["bakery", "farm"] }, at("2026-07-06T15:00")).length, 2);
  assert.strictEqual(S.filterShops(pois, { kinds: [] }, at("2026-07-06T15:00")).length, 3);
});

test("filterShops: jetzt offen laesst Unbekanntes drin", () => {
  const r = S.filterShops(pois, { openNow: true }, at("2026-07-06T15:00"));
  assert.deepStrictEqual(r.map((p) => p.flags.kind), ["discount", "farm"]);
  assert.strictEqual(S.filterShops(pois, { openNow: true }, at("2026-07-05T10:00")).length, 1);   // Sonntag: nur farm
});

test("nextOpenText", () => {
  const h = "Mo-Sa 07:00-20:00";
  assert.strictEqual(S.nextOpenText(h, at("2026-07-06T15:00")), "offen bis 20:00");
  assert.strictEqual(S.nextOpenText(h, at("2026-07-06T05:00")), "öffnet 07:00");
  assert.strictEqual(S.nextOpenText(h, at("2026-07-06T21:00")), "geschlossen");
  assert.strictEqual(S.nextOpenText(h, at("2026-07-05T12:00")), "geschlossen");
  assert.strictEqual(S.nextOpenText("Mist", at("2026-07-06T15:00")), "Zeiten prüfen");
  assert.strictEqual(S.nextOpenText("", at("2026-07-06T15:00")), "Zeiten prüfen");
});

test("shopKindLabel", () => {
  assert.strictEqual(S.shopKindLabel("discount"), "Discounter");
  assert.strictEqual(S.shopKindLabel("farm"), "Hofladen");
});

test("stageHint: Rohzeiten statt offen/zu (Etappe liegt in der Zukunft)", () => {
  const ls = { name: "Lidl", label: "Discounter", kind: "discount", km_before: 3, hours: "Mo-Sa 07:00-20:00" };
  assert.strictEqual(S.stageHint(ls), "🛒 Letzter Einkauf vor dem Ziel: Lidl (Discounter), 3,0 km vorher – Mo-Sa 07:00-20:00");
  assert.match(S.stageHint({ ...ls, hours: null }), /Zeiten unbekannt$/);
  assert.strictEqual(S.stageHint(null), null);
});

test("shopPopup (0.54.0: Detail-Karte): OSM ansehen + ergänzen bei node/1, keine OSM-Links bei Rohwert", () => {
  // shops.js und detailcard.js im Browser-Zweig mit winzigem DOM-Ersatz laden
  const vm = require("node:vm"), fs = require("node:fs");
  const node = (tag) => ({ tag, kids: [], dataset: {}, append(...k) { this.kids.push(...k); }, addEventListener() {},
    insertAdjacentHTML() {}, get childElementCount() { return this.kids.length; }, get firstChild() { return this.kids[0]; } });
  const ctx = {
    document: { createElement: node, getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] },
    localStorage: { getItem: () => null, setItem() {} }, CampFilter: globalThis.CampFilter,
    fmt: (n) => String(n), $: () => null, layers: {}, window: null,
  };
  ctx.window = ctx; ctx.globalThis = ctx;
  for (const f of ["detailcard.js", "shops.js"]) vm.runInNewContext(fs.readFileSync(require.resolve(`../../app/static-rig/${f}`), "utf8"), ctx);
  const links = (n) => (n.href ? [n.href] : []).concat(...(n.kids || []).filter((k) => typeof k === "object").map(links));
  const osm = (n) => links(n).filter((h) => h.includes("openstreetmap.org"));
  const mk = (osmId, flags = {}) => ({ group: "shop", km: 1, off: 50, name: "Lidl", label: "Discounter", lat: 52, lon: 13, osm: osmId,
    flags: { kind: "discount", hours: null, ...flags } });
  assert.deepStrictEqual(osm(ctx.ShopList.shopPopup(mk("node/1"))),
    ["https://www.openstreetmap.org/node/1", "https://www.openstreetmap.org/edit?node=1"]);
  assert.deepStrictEqual(osm(ctx.ShopList.shopPopup(mk("n1"))), []);
  const l = links(ctx.ShopList.shopPopup(mk("node/2", { phone: "+49 30 1234567", website: "https://lidl.de" })));
  assert.ok(l.includes("tel:+49301234567") && l.includes("https://lidl.de"));
});

test("dedupeHints: Laden-Hinweis nur einmal, andere Notizen bleiben", () => {
  const h = (km) => ({ t: "shop", label: "Einkaufen: bisher nur Deutschland", km });
  const other = { t: "x", label: "A", km: 2 };
  const r = S.dedupeHints([h(0), other, h(50), { t: "shop", label: "Einkaufsdaten fehlen", km: 60 }, { t: "shop", label: "Einkaufsdaten fehlen", km: 90 }]);
  assert.deepStrictEqual(r.map((n) => n.km), [0, 2, 60]);
});

test("toWaypoint: Zwischenpunkt, kein Nacht-Stopp, Namens-Fallback", () => {
  const p = { lat: 1, lon: 2, name: "Lidl", osm: "node/5", label: "Discounter", flags: { kind: "discount" } };
  assert.deepStrictEqual(S.toWaypoint(p), { lat: 1, lon: 2, name: "Lidl", stop: false, poi: { osm: "node/5", what: "shop", kind: "discount" } });
  assert.strictEqual(S.toWaypoint({ ...p, name: "" }).name, "Discounter");
  assert.strictEqual(S.toWaypoint({ lat: 1, lon: 2, flags: { brand: "Edeka" } }).name, "Edeka");
  assert.strictEqual(S.toWaypoint({ lat: 1, lon: 2 }).name, "Laden");
});

test("isInTour: OSM-Id oder Koordinate", () => {
  const p = { lat: 1, lon: 2, osm: "node/5" };
  assert.strictEqual(S.isInTour(p, [{ lat: 9, lon: 9 }]), false);
  assert.strictEqual(S.isInTour(p, [{ lat: 9, lon: 9, poi: { osm: "node/5" } }]), true);
  assert.strictEqual(S.isInTour(p, [{ lat: 1, lon: 2 }]), true);
  assert.strictEqual(S.isInTour({ lat: 1, lon: 2 }, [{ lat: 3, lon: 4, poi: { osm: null } }]), false);
  assert.strictEqual(S.isInTour(p, []), false);
});

test("toWaypoint/poiName: Plätze, Tankstellen, Rastplätze (0.44.0)", () => {
  assert.deepStrictEqual(S.toWaypoint({ lat: 1, lon: 2, name: "Aral", osm: "node/7" }, "fuel"),
    { lat: 1, lon: 2, name: "Aral", stop: false, poi: { osm: "node/7", what: "fuel", kind: null } });
  assert.strictEqual(S.poiName({ lat: 1, lon: 2 }, "fuel"), "Tankstelle");
  assert.strictEqual(S.poiName({ lat: 1, lon: 2, label: "Rastplatz mit WC" }, "rest"), "Rastplatz mit WC");
  assert.strictEqual(S.poiName({ lat: 1, lon: 2 }, "rest"), "Rastplatz");
  assert.strictEqual(S.poiName({ lat: 1, lon: 2 }, "camp"), "Platz");
  assert.strictEqual(S.poiName({ lat: 1, lon: 2, flags: { brand: "Shell" } }, "fuel"), "Tankstelle");   // Marke nur bei Läden
  assert.strictEqual(S.isInTour({ lat: 5, lon: 6, osm: "node/7" }, [S.toWaypoint({ lat: 1, lon: 2, osm: "node/7" }, "fuel")]), true);
});

test("tourLabel", () => {
  assert.strictEqual(S.tourLabel(false), "➕ In die Tour");
  assert.strictEqual(S.tourLabel(true), "✓ in der Tour");
});
