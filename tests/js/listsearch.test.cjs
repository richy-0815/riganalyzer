// node --test tests/js/  –  Suchfeld über den Listen
const test = require("node:test");
const assert = require("node:assert");
const S = require("../../app/static-rig/listsearch.js");

test("matches: leer = alles, Groß/klein und Umlaute egal", () => {
  assert.equal(S.matches("Aral Tankstelle", ""), true);
  assert.equal(S.matches("Aral Tankstelle", "   "), true);
  assert.equal(S.matches("ARAL Tankstelle", "aral"), true);
  assert.equal(S.matches("Stellplatz Müritz", "muritz"), true);
  assert.equal(S.matches("Stellplatz Muritz", "müritz"), true);
  assert.equal(S.matches("Straße am See", "strasse"), true);
});

test("matches: mehrere Wörter müssen alle vorkommen", () => {
  assert.equal(S.matches("Stellplatz am See · Wasser · Entsorgung", "see wasser"), true);
  assert.equal(S.matches("Stellplatz am See · Entsorgung", "see wasser"), false);
});

test("countText", () => {
  assert.equal(S.countText(3, 20), "3 von 20");
  assert.equal(S.countText(0, 0), "");
});
