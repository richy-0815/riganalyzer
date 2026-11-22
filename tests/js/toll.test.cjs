// node --test tests/js/  –  Maut je Land
const test = require("node:test");
const assert = require("node:assert");
const T = require("../../app/static-rig/toll.js");

const womo = { height: 2.9, zgg: 3.5, trailer: false };

test("Mautklasse Frankreich", () => {
  assert.strictEqual(T.classFR(womo), 2);                                   // typisches Wohnmobil
  assert.strictEqual(T.classFR({ height: 1.6, zgg: 2, trailer: false }), 1);  // Pkw
  assert.strictEqual(T.classFR({ height: 1.6, zgg: 2, trailer: true }), 2);   // Pkw + Wohnwagen
  assert.strictEqual(T.classFR({ height: 3.2, zgg: 3.5 }), 3);                // über 3 m
  assert.strictEqual(T.classFR({ height: 2.9, zgg: 4.5 }), 3);                // über 3,5 t
  assert.strictEqual(T.classFR({ height: 2.9, zgg: 4.5, trailer: true }), 4);
});

test("Mautklasse Italien", () => {
  assert.strictEqual(T.classIT(womo), "B");
  assert.strictEqual(T.classIT({ height: 1.5, zgg: 2 }), "A");
  assert.strictEqual(T.classIT({ height: 1.5, zgg: 2, trailer: true }), 3);
});

test("Frankreich: Maut-km × Klassensatz, nur mautpflichtige km", () => {
  const r = T.country({ cc: "FR", km: 900, motorway_km: 800, toll_km: 700 }, womo);
  assert.ok(Math.abs(r.eur - 700 * 0.147) < 0.01);
  assert.strictEqual(r.exact, false);
  assert.match(r.text, /^ca\. 103 € Maut$/);
  assert.match(r.detail, /Klasse 2 · 700 km/);
  assert.strictEqual(T.country({ cc: "FR", km: 50, motorway_km: 0, toll_km: 0 }, womo).eur, 0);
});

test("Schweiz: Vignette bzw. PSVA", () => {
  const base = { cc: "CH", km: 200, motorway_km: 150, toll_km: 0 };
  assert.strictEqual(T.country(base, womo).chf, 40);
  assert.strictEqual(T.country(base, { ...womo, trailer: true }).chf, 80);   // Anhänger braucht eigene
  assert.strictEqual(T.country(base, { ...womo, zgg: 4.2 }, 2).chf, 25);    // PSVA Minimum
  assert.strictEqual(T.country(base, { ...womo, zgg: 4.2 }, 12).chf, 39);   // 12 × 3,25
  assert.strictEqual(T.country(base, { ...womo, zgg: 4.2 }, 30).chf, 58.5); // Höchstbetrag
  assert.strictEqual(T.country({ ...base, motorway_km: 0 }, womo).chf, 0);  // ohne Autobahn keine Vignette
});

test("Österreich: Vignette nach Tagen, über 3,5 t nicht berechnet", () => {
  const base = { cc: "AT", km: 300, motorway_km: 250, toll_km: 250 };
  assert.strictEqual(T.country(base, womo, 1).eur, 9.6);
  assert.strictEqual(T.country(base, womo, 4).eur, 12.8);
  assert.strictEqual(T.country(base, womo, 20).eur, 32);
  const heavy = T.country(base, { ...womo, zgg: 4.2 });
  assert.strictEqual(heavy.eur, 0);
  assert.match(heavy.text, /GO-Maut/);
});

test("Reise: Summe in € und CHF, ca. nur wenn geschätzt", () => {
  const cs = [{ cc: "DE", km: 500, motorway_km: 450, toll_km: 0 }, { cc: "CH", km: 250, motorway_km: 200, toll_km: 0 },
    { cc: "FR", km: 700, motorway_km: 650, toll_km: 600 }];
  const t = T.trip(cs, womo);
  assert.strictEqual(t.chf, 40);
  assert.strictEqual(t.approx, true);
  assert.strictEqual(t.total, "ca. 88 € + 40 CHF");
  assert.strictEqual(T.trip([{ cc: "DE", km: 100, motorway_km: 90 }], womo).total, "");
});

// ---- Spanien-Klassen, Sondermaut (Tunnel/Brücken) aus sondermaut.json
const RULES = require("../../app/sondermaut.json");
const gespann = { height: 2.87, zgg: 3.5, trailer: true };

test("Spanien: Wohnmobil bis 3,5 t ist Ligeros, auch mit Anhänger; darüber Pesados 1", () => {
  const base = { cc: "ES", km: 300, motorway_km: 250, toll_km: 100 };
  assert.match(T.country(base, womo).detail, /^Ligeros/);
  assert.ok(Math.abs(T.country(base, womo).eur - 100 * T.TOLL.km.ES[1]) < 0.01);
  assert.match(T.country(base, gespann).detail, /^Ligeros/);
  assert.match(T.country(base, { ...womo, zgg: 4.2 }).detail, /^Pesados 1/);
});

test("Sondermaut: Klasse je Schema", () => {
  assert.strictEqual(T.specialClass("hoehe", womo), "2");
  assert.strictEqual(T.specialClass("hoehe", { height: 1.6, zgg: 2 }), "1");
  assert.strictEqual(T.specialClass("hoehe", { height: 3.2, zgg: 3.5, trailer: true }), "4");
  assert.strictEqual(T.specialClass("FR", gespann), "2");
  assert.strictEqual(T.specialClass("womo", womo), "womo");
  assert.strictEqual(T.specialClass("womo", { height: 1.6, trailer: true }), "pkw");
  assert.strictEqual(T.specialClass("at", gespann), "bis35");
  assert.strictEqual(T.specialClass("at", { ...womo, zgg: 4.2 }), null);
});

test("Sondermaut: Millau nach Saison, Mont-Blanc Klasse 2, Gespann", () => {
  const hits = [{ id: "millau", km: 120, date: "2027-05-26" }, { id: "millau", km: 900, date: "2027-07-01" }, { id: "montblanc", km: 400 }];
  const rows = T.special(hits, RULES, gespann);
  assert.deepStrictEqual(rows.map((r) => r.eur), [17, 20.7, 73.4]);
  assert.ok(rows.every((r) => r.exact));
  assert.match(rows[0].detail, /Klasse 2/);
  // ohne Datum: Sommerpreis (lieber zu viel als zu wenig), mit Hinweis
  const nod = T.special([{ id: "millau", km: 1 }], RULES, gespann)[0];
  assert.strictEqual(nod.eur, 20.7);
  assert.match(nod.detail, /außerhalb 15\.06\.–15\.09\.: 17,00 €/);
});

test("Sondermaut: CHF, über 3,5 t in Österreich nicht berechnet, unbekannte Id ignoriert", () => {
  const [gsb] = T.special([{ id: "gsb", km: 5 }], RULES, gespann);
  assert.strictEqual(gsb.chf, 48.5);
  assert.strictEqual(gsb.exact, false);
  const [br] = T.special([{ id: "brenner", km: 5 }], RULES, { ...womo, zgg: 4.2 });
  assert.strictEqual(br.eur, 0);
  assert.match(br.text, /GO-Maut/);
  assert.deepStrictEqual(T.special([{ id: "gibtsnicht", km: 1 }], RULES, womo), []);
  assert.deepStrictEqual(T.special([{ id: "millau", km: 1 }], null, womo), []);
});

test("Reise mit Sondermaut: Summe, Maut-km der Brücke nicht doppelt", () => {
  const cs = [{ cc: "FR", km: 700, motorway_km: 650, toll_km: 3 }, { cc: "CH", km: 250, motorway_km: 200, toll_km: 0 }];
  const sp = T.special([{ id: "millau", km: 50, date: "2027-05-26" }, { id: "gsb", km: 600 }], RULES, gespann);
  const t = T.trip(cs, gespann, () => 1, sp);
  assert.strictEqual(t.rows[0].eur, 0);            // A75 nur am Viadukt mautpflichtig -> zählt nur der Brückenpreis
  assert.strictEqual(t.eur, 17);
  assert.strictEqual(t.chf, 80 + 48.5);            // 2 Vignetten + Tunnel
  assert.strictEqual(t.approx, true);              // St. Bernhard nicht amtlich
  assert.strictEqual(t.total, "ca. 17 € + 128,50 CHF");
  assert.strictEqual(t.special.length, 2);
});
