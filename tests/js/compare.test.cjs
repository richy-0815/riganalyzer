// node --test tests/js/  –  Routenvergleich: Route rausnehmen, Spaltennamen
const test = require("node:test");
const assert = require("node:assert");
const C = require("../../app/static-ride/compare.js");

test("aktive Route nach dem Rausnehmen", () => {
  assert.strictEqual(C.nextActive(2, 0, 3), 1);   // davor entfernt: rueckt nach links
  assert.strictEqual(C.nextActive(0, 2, 3), 0);   // dahinter entfernt: bleibt
  assert.strictEqual(C.nextActive(1, 1, 3), 1);   // aktive entfernt: Nachbar rechts uebernimmt
  assert.strictEqual(C.nextActive(2, 2, 3), 1);   // aktive war die letzte: links daneben
});

test("Spaltenname: ohne .gpx und Unterstriche, sonst ganz (umbricht per CSS)", () => {
  assert.strictEqual(C.colName("MTD_Harz/Kyffhäuser-Tour.gpx", 0), "MTD Harz/Kyffhäuser-Tour");
  assert.strictEqual(C.colName("", 2), "Route 3");
  assert.strictEqual(C.colName("x".repeat(80), 0).length, 48);
});

test("Farbe je Tour: feste Reihenfolge, wiederholt sich, keine Ampelfarben (0.18)", () => {
  assert.strictEqual(C.colorOf(0), C.COLORS[0]);
  assert.strictEqual(C.colorOf(C.COLORS.length), C.COLORS[0]);
  assert.ok(C.COLORS.length >= 5 && new Set(C.COLORS).size === C.COLORS.length);
  assert.ok(!C.COLORS.some((c) => /^#(2e7d32|c62828|f9a825)$/i.test(c)));   // nicht mit der Bewertungsskala verwechselbar
});
