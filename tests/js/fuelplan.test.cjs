// node --test tests/js/  –  Tankplan mit Preisklassen und Abstechern
const test = require("node:test");
const assert = require("node:assert");
const F = require("../../app/static-rig/fuelplan.js");

const st = (km, cls, extra = {}) => ({ km, name: `${cls}@${km}`, flags: { cls, ...extra } });
const base = { total: 900, cap: 90, consPerKm: 0.12, reserve: 15, startL: 90, base: 2.0 };

test("Preis: Klasse als Auf-/Abschlag, echter Preis hat Vorrang", () => {
  assert.strictEqual(F.priceOf(st(1, "rast"), 2), 2.3);
  assert.strictEqual(F.priceOf(st(1, "discount"), 2), 1.96);
  assert.strictEqual(F.priceOf({ ...st(1, "rast"), price: 1.79 }, 2), 1.79);
  assert.strictEqual(F.priceOf({ km: 1 }, 2), 2);
});

test("Umweg kostet Sprit und Zeit", () => {
  const s = st(1, "discount", { detour_km: 4 });
  // 4 km * 0,12 l/km * 2 € + (3 + 6 min) * 0,25 €
  assert.ok(Math.abs(F.detourCost(s, 0.12, 2) - (0.96 + 2.25)) < 1e-9);
  assert.strictEqual(F.detourCost(st(1, "frei"), 0.12, 2), 0);
});

test("Raststaette verliert gegen guenstigen Abstecher im selben Fenster", () => {
  const stations = [st(500, "rast", { motorway: true }), st(480, "discount", { detour_km: 3 })];
  const p = F.planTank({ ...base, stations });
  assert.strictEqual(p.stops[0].name, "discount@480");
  assert.ok(p.stops[0].saving > 15, `Ersparnis ${p.stops[0].saving}`);
  assert.strictEqual(p.stops[0].estimated, true);
  // der Abstecher kostet Liter: zurueck auf der Autobahn ist der Tank nicht ganz voll
  assert.ok(p.stops[0].liters > 90 - (90 - 480 * 0.12));
});

test("weiter Abstecher lohnt sich nicht bei fast gleichem Preis", () => {
  const stations = [st(500, "frei"), st(490, "discount", { detour_km: 8 })];
  const p = F.planTank({ ...base, stations });
  assert.strictEqual(p.stops[0].name, "frei@500");
  assert.strictEqual(p.stops[0].saving, null);
});

test("nicht zu frueh tanken: guenstig am Anfang zaehlt nicht", () => {
  const stations = [st(50, "discount"), st(500, "marke")];
  const p = F.planTank({ ...base, stations });
  assert.strictEqual(p.stops[0].km, 500);
});

test("Luecke wird gemeldet, Reserve wird angeknabbert", () => {
  const p = F.planTank({ ...base, stations: [st(700, "frei")] });   // Reichweite bis Reserve: 625 km
  assert.strictEqual(p.gaps.length, 1);
  assert.strictEqual(p.stops[0].km, 700);
});

test("Lkw-Saeule bevorzugt, Autobahn gemieden wenn moeglich", () => {
  const stations = [st(460, "discount"), st(470, "marke", { truck: true }), st(500, "rast", { motorway: true })];
  const p = F.planTank({ ...base, stations, prefer: (s) => s.flags.truck, avoid: F.isMw });
  assert.strictEqual(p.stops[0].km, 470);
});

test("withLivePrices: echter Preis per OSM-Id, Rest bleibt geschaetzt (0.17)", () => {
  const F = require("../../app/static-rig/fuelplan.js");
  const st = [{ osm: "way/1", km: 10, flags: {} }, { osm: "node/2", km: 20, flags: {} }];
  const out = F.withLivePrices(st, { "way/1": { diesel: 1.719, open: false, name: "ARAL", at: 1800000000 } });
  assert.strictEqual(out[0].price, 1.719);
  assert.strictEqual(out[0].live.open, false);
  assert.strictEqual(out[1].price, undefined);
  assert.strictEqual(st[0].price, undefined);                       // Original unveraendert
  const plan = F.planTank({ total: 30, stations: out, cap: 80, consPerKm: 0.12, reserve: 10, startL: 11, base: 1.85 });
  const s = plan.stops[0];
  assert.strictEqual(s.estimated, false);
  assert.strictEqual(s.price, 1.719);
});

test("priceCandidates: nur rund um die geplanten Stopps, Stopps zuerst, gedeckelt (0.17.1)", () => {
  const F = require("../../app/static-rig/fuelplan.js");
  const st = Array.from({ length: 100 }, (_, i) => ({ osm: `n/${i}`, km: i * 10, lat: 50, lon: 9 }));
  const ids = F.priceCandidates(st, [{ km: 300, osm: "n/30" }, { km: 700, osm: "n/70" }], 10, 60).map((x) => x[0]);
  assert.deepStrictEqual(ids.slice(0, 2), ["n/30", "n/70"]);
  assert.deepStrictEqual(ids.slice(2).sort(), ["n/29", "n/31", "n/69", "n/71"].sort());
  assert.strictEqual(F.priceCandidates(st, st.map((s) => ({ km: s.km, osm: s.osm })), 10, 60).length, 60);
});

test("calibrate: Schaetzung an echte Preise in der Naehe angleichen (0.23.0)", () => {
  const F = require("../../app/static-rig/fuelplan.js");
  const live = (km, price, cls) => ({ km, price, live: { diesel: price }, flags: { cls } });
  const st = [live(100, 2.38, "frei"), live(120, 2.42, "marke"), { km: 130, flags: { cls: "discount" } },
    { km: 400, flags: { cls: "frei" } }, live(390, 2.0, "frei")];
  const c = F.calibrate(st);
  // km 130: Median der bereinigten Basen 2,38 und 2,39 (= 2,42 − 0,03) → 2,385; dazu Abschlag „günstig“ −0,04
  assert.ok(Math.abs(c[2].localBase - 2.385) < 1e-9);
  assert.ok(Math.abs(F.priceOf(c[2], 2.10) - 2.345) < 1e-9);
  // km 400: nur einer bis 80 km -> weiter geschaut (bis 400 km): Median 2,38, nur nach oben angleichen
  assert.strictEqual(c[3].localWide, true);
  assert.ok(Math.abs(F.priceOf(c[3], 2.10) - 2.38) < 1e-9);
  assert.strictEqual(F.priceOf(c[3], 2.50), 2.50);            // Profil teurer: bleibt
  assert.strictEqual(F.calibrate([live(0, 2.4, "frei"), { km: 900, flags: {} }])[1].localBase, undefined);   // zu weit / zu wenig
  assert.strictEqual(c[0], st[0]);                            // echte Preise unveraendert
});


test("planTank: bei fast gleichem Preis die Tankstelle mit echtem Preis (unsure, 0.23.0)", () => {
  const F = require("../../app/static-rig/fuelplan.js");
  const sts = [{ km: 500, flags: { cls: "frei" } }, { km: 520, price: 2.13, live: { diesel: 2.13 }, flags: { cls: "frei" } }];
  const opt = { total: 900, stations: sts, cap: 90, consPerKm: 0.12, reserve: 15, startL: 90, base: 2.10 };
  assert.strictEqual(F.planTank(opt).stops[0].km, 500);                  // ohne Aufschlag: geschaetzt 2,10 gewinnt
  assert.strictEqual(F.planTank({ ...opt, unsure: 0.05 }).stops[0].km, 520);
});

test("12-Uhr-Regel: vor 12 tanken, wenn es in Reichweite geht (adjust, 0.23.0)", () => {
  const F = require("../../app/static-rig/fuelplan.js");
  assert.ok(F.deHourOffset(11.5) < 0 && F.deHourOffset(12.2) > 0.1 && F.deHourOffset(21) === 0);
  const sts = [{ km: 450, clock: 11.6, flags: { cls: "frei" } }, { km: 520, clock: 12.4, flags: { cls: "frei" } }];
  const opt = { total: 900, stations: sts, cap: 90, consPerKm: 0.12, reserve: 15, startL: 90, base: 2.10 };
  assert.strictEqual(F.planTank(opt).stops[0].km, 520);                               // sonst: spaeter tanken
  assert.strictEqual(F.planTank({ ...opt, adjust: (s) => F.deHourOffset(s.clock) }).stops[0].km, 450);
});

test("Landesschnitt (0.32.0): Basis der Schaetzung, kein echter Preis, Kalibrierung laesst ihn stehen", () => {
  const live = { "node/1": { diesel: 2.5, avg: true, cc: "DK", src: "Landesschnitt EU" },
                 "node/2": { diesel: 1.70, cc: "DE", src: "Tankerkönig" } };
  const s = F.withLivePrices([{ ...st(10, "marke"), osm: "node/1" }, { ...st(20, "frei"), osm: "node/2" }, { ...st(30, "frei"), osm: "node/3" }], live);
  assert.strictEqual(s[0].price, undefined);
  assert.strictEqual(s[0].live, undefined);
  assert.strictEqual(s[0].localBase, 2.5);
  assert.strictEqual(s[1].price, 1.70);
  const c = F.calibrate(s, 80, 1);
  assert.strictEqual(c[0].localBase, 2.5);                    // nicht vom deutschen Echtpreis ueberschrieben
  assert.ok(Math.abs(F.priceOf(c[0], 2.0) - (2.5 + F.DELTA.marke)) < 1e-9);
});
