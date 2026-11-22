// node --test tests/js/  –  Wartetext
const test = require("node:test");
const assert = require("node:assert");
const Q = require("../../app/static/queuetext.js");

test("label: Server-Text bei Warteschlange, sonst alter Text bzw. Job-Text", () => {
  assert.equal(Q.label({ state: "queued", msg: "Platz 2 in der Warteschlange · noch ca. 3 Min." }), "Platz 2 in der Warteschlange · noch ca. 3 Min.");
  assert.equal(Q.label({ state: "queued", msg: "wartet" }), "wartet auf einen freien Platz …");
  assert.equal(Q.label({ state: "queued" }), "wartet auf einen freien Platz …");
  assert.equal(Q.label({ state: "running", msg: "Etappe 1/2" }), "Etappe 1/2");
});
