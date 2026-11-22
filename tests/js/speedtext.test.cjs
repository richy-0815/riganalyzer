// node --test tests/js/  –  Tempo-Satz je Land aus speedrules.json (RigAnalyzer)
const test = require("node:test");
const assert = require("node:assert");
const S = require("../../app/static-rig/speedtext.js");
const RULES = require("../../app/speedrules.json");

test("Gespann in Frankreich", () => {
  assert.strictEqual(S.speedLine(RULES, "FR", { trailer: true, zgg: 3.5 }),
    "Gespann: Autobahn 90, Schnellstraße 80, außerorts 80 km/h (innerorts laut Schild).");
});

test("Tempo-100 nur in Deutschland", () => {
  assert.match(S.speedLine(RULES, "DE", { trailer: true, tempo100: true, zgg: 3.5 }), /^Gespann mit Tempo-100-Zulassung: Autobahn 100/);
  assert.match(S.speedLine(RULES, "FR", { trailer: true, tempo100: true, zgg: 3.5 }), /^Gespann: Autobahn 90/);
});

test("schwer und Pkw-artig", () => {
  assert.match(S.speedLine(RULES, "IT", { trailer: false, zgg: 5 }), /^Über 3,5 t: Autobahn 100/);
  assert.strictEqual(S.speedLine(RULES, "DE", { trailer: false, zgg: 3.5 }), "Bis 3,5 t ohne Anhänger: wie Pkw.");
});

test("unbekanntes Land bzw. keine Tabelle", () => {
  assert.strictEqual(S.speedLine(RULES, "PL", { trailer: true }), "");
  assert.strictEqual(S.speedLine(null, "DE", { trailer: true }), "");
});
