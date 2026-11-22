// node --test tests/js/  –  Touren teilen
const test = require("node:test");
const assert = require("node:assert");
const T = require("../../app/static-rig/teilen.js");

test("stateText: alle Zustände", () => {
  assert.equal(T.stateText("offen"), "offen");
  assert.equal(T.stateText("angenommen"), "✓ angenommen");
  assert.equal(T.stateText("abgelehnt"), "abgelehnt");
  assert.equal(T.stateText("zurückgezogen"), "zurückgezogen");
  assert.equal(T.stateText("abgelaufen"), "abgelaufen");
});

test("inboxLine: Name, Absender, Etappen, Datum", () => {
  assert.equal(T.inboxLine({ from: "olaf", name: "Lausitz", created: "2026-11-12T10:00:00", stages: 1 }),
    "„Lausitz“ von olaf · 1 Etappe · 12.11.");
  assert.equal(T.inboxLine({ from: "olaf", name: "X", created: "2026-01-05T10:00:00", stages: 3 }),
    "„X“ von olaf · 3 Etappen · 05.01.");
});

test("sharedWith: nur passende Tour, mehrere, leer", () => {
  const out = [{ trip_id: "a", to: "otto", state: "offen" }, { trip_id: "b", to: "x", state: "offen" },
    { trip_id: "a", to: "mia", state: "angenommen" }];
  assert.equal(T.sharedWith(out, "a"), "geteilt mit otto (offen), mia (✓ angenommen)");
  assert.equal(T.sharedWith(out, "c"), "");
  assert.equal(T.sharedWith([], "a"), "");
});
