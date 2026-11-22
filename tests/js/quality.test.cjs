// node --test tests/js/  –  Datenqualitaet: Etappen zusammenfassen, Anzeige
const test = require("node:test");
const assert = require("node:assert");
const Q = require("../../app/static/quality.js");

const q = (o) => ({ matched: 100, surface: 80, speed: 60, width: 50, elevation: true, source: "lokal",
  reconstructed: false, osm_date: "2026-10-25", score: 80, grade: "gut", ...o });

test("Etappen nach km gewichtet, Zaehler addiert, aeltester Kartenstand", () => {
  const m = Q.merge([
    { km: 300, quality: q({ surface: 100, fuel: { n: 10, diesel: 50, adblue: 10, hours: 100 } }) },
    { km: 100, quality: q({ surface: 60, score: 40, elevation: false, osm_date: "2026-10-20", fuel: { n: 30, diesel: 90, adblue: 30, hours: 0 } }) },
  ]);
  assert.strictEqual(m.surface, 90);
  assert.strictEqual(m.score, 70);
  assert.strictEqual(m.grade, "brauchbar");
  assert.strictEqual(m.elevation, false);
  assert.strictEqual(m.osm_date, "2026-10-20");
  assert.deepStrictEqual(m.fuel, { n: 40, diesel: 80, adblue: 25, hours: 25 });
});

test("ohne Daten: nichts", () => {
  assert.strictEqual(Q.merge([{ km: 10, quality: null }]), null);
  assert.strictEqual(Q.html(null), "");
});

test("Anzeige: Note, Balken, Kartenalter", () => {
  const h = Q.html(q({ grade: "dünn", score: 40 }), new Date("2026-10-27T12:00"));
  assert.match(h, /q-thin/);
  assert.match(h, /Belag bekannt/);
  assert.match(h, /vor 2 Tagen/);
});
