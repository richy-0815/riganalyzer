const test = require("node:test");
const assert = require("node:assert");
const K = require("../../app/static/konto-cards.js");

const DEF = ["touren", "zugang", "daten", "rignavi"];

test("ohne gemerkte Anordnung: Standard", () => {
  assert.deepStrictEqual(K.arrange(null, DEF), DEF);
});
test("gemerkte Reihenfolge gilt, Unbekanntes faellt raus", () => {
  assert.deepStrictEqual(K.arrange(["daten", "boese", "touren", "zugang", "rignavi"], DEF), ["daten", "touren", "zugang", "rignavi"]);
});
test("neue Kachel kommt an ihren Standardplatz (vor die naechste bekannte)", () => {
  assert.deepStrictEqual(K.arrange(["rignavi", "zugang", "touren"], DEF), ["daten", "rignavi", "zugang", "touren"]);
  assert.deepStrictEqual(K.arrange(["zugang", "rignavi"], DEF), ["touren", "zugang", "daten", "rignavi"]);
});
test("Ride: Kacheln, die es dort nicht gibt, stoeren nicht", () => {
  assert.deepStrictEqual(K.arrange(["daten", "touren", "zugang"], ["zugang", "daten"]), ["daten", "zugang"]);
});
test("move: hoch/runter, an den Raendern nichts", () => {
  assert.deepStrictEqual(K.move(["a", "b", "c"], "b", -1), ["b", "a", "c"]);
  assert.deepStrictEqual(K.move(["a", "b", "c"], "c", 1), ["a", "b", "c"]);
  assert.deepStrictEqual(K.move(["a", "b", "c"], "a", -1), ["a", "b", "c"]);
});
test("newer: Login-Stand nur, wenn juenger als der im Browser", () => {
  assert.strictEqual(K.newer({ at: 5 }, { at: 3 }).at, 5);
  assert.strictEqual(K.newer({ at: 2 }, { at: 3 }).at, 3);
  assert.strictEqual(K.newer(null, { at: 3 }).at, 3);
  assert.strictEqual(K.newer({ at: 1 }, null).at, 1);
  assert.strictEqual(K.newer(null, null), null);
});
