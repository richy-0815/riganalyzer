// node --test tests/js/  –  Aufenthalte und Fahrpausen
const test = require("node:test");
const assert = require("node:assert");
const A = require("../../app/static-rig/aufenthalt.js");

test("fmt", () => {
  assert.deepStrictEqual([0, 15, 45, 60, 90, 120, 135].map(A.fmt), ["0 min", "15 min", "45 min", "1 h", "1:30 h", "2 h", "2:15 h"]);
});

test("byStage: Aufenthalte je Etappe mit km ab Etappenstart, Nacht beginnt neue Etappe", () => {
  const pts = [{}, { stay: 120, name: "Dom" }, { stop: true, stay: 60 }, {}, { stay: 30, name: "See" }, {}];
  const legs = [{ km: 50, to: 1 }, { km: 70, to: 2 }, { km: 40, to: 4 }, { km: 25, to: 5 }];
  assert.deepStrictEqual(A.byStage(pts, legs), [[{ km: 50, min: 120, name: "Dom" }], [{ km: 40, min: 30, name: "See" }]]);
});

test("byStage: ohne Abschnitte oder Aufenthalte leer", () => {
  assert.deepStrictEqual(A.byStage([{}, {}], [{ km: 10, to: 1 }]), [[]]);
  assert.deepStrictEqual(A.byStage([{}, {}], null), [[]]);
  assert.strictEqual(A.sumMin([{ min: 30 }, { min: 90 }]), 120);
});

test("pauseHours: geschätzt wie bisher, 20 min je volle 2 h, ohne", () => {
  assert.strictEqual(A.pauseHours(250, 4), 0.75);
  assert.strictEqual(A.pauseHours(800, 9), 1.75);
  assert.strictEqual(A.pauseHours(300, 4.5, "2h20"), 40 / 60);
  assert.strictEqual(A.pauseHours(300, 4, "2h20"), 20 / 60);    // genau 4 h: Pause nach 2 h, am Ziel keine
  assert.strictEqual(A.pauseHours(100, 1.5, "2h20"), 0);
  assert.strictEqual(A.pauseHours(500, 6, "none"), 0);
});

test("shiftPoints: Punkte hinter dem Aufenthalt später, davor unverändert", () => {
  const p = [[48, 11, "2027-05-24T06:00:00.000Z", 0], [48.1, 11.1, "2027-05-24T07:00:00.000Z", 60], [48.2, 11.2, "2027-05-24T08:00:00.000Z", 120]];
  const out = A.shiftPoints(p, [{ km: 60, min: 90 }]);
  assert.deepStrictEqual(out.map((x) => x[2]), ["2027-05-24T06:00:00.000Z", "2027-05-24T08:30:00.000Z", "2027-05-24T09:30:00.000Z"]);
  assert.strictEqual(A.shiftPoints(p, []), p);
  assert.deepStrictEqual(A.shiftPoints(p, [{ km: 10, min: 30 }], 100)[1][2], "2027-05-24T07:00:00.000Z");   // k0 verschiebt die Zählung
});
