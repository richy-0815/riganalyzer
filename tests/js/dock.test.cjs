// Ergebnis-Karten als Dashboard (Ride + Rig): reine Anordnungs-Logik aus dock.js.
const test = require("node:test");
const assert = require("node:assert");
const D = require("../../app/static/dock.js");

const RIG = D.DEFAULTS.rig, RIDE = D.DEFAULTS.ride;
const all = (def) => def.left.concat(def.right);

test("Rig-Standard unveraendert (wie bis 0.37.4 in rig.js)", () => {
  assert.deepStrictEqual(RIG, {
    left: ["upload", "planner", "vehicle", "result", "verdict", "fuel", "profile", "list", "rules", "quality"],
    right: ["trip"], collapsed: [] });
});

test("Ride-Standard = heutige Reihenfolge, Streckenband unter der Karte", () => {
  assert.deepStrictEqual(RIDE, {
    left: ["upload", "planner", "result", "profiles", "highlights", "weather", "list", "quality", "compare"],
    right: ["band"], collapsed: [] });
});

for (const [name, def] of [["rig", RIG], ["ride", RIDE]]) {
  test(`${name}: ohne gemerkte Anordnung gilt der Standard`, () => {
    assert.deepStrictEqual(D.arrange(null, def, all(def)), def);
    assert.deepStrictEqual(D.arrange(def, def, all(def)), def);
  });
}

test("neue Karte (im Gemerkten noch nicht dabei) kommt an ihren Standardplatz", () => {
  const saved = { left: ["upload", "planner", "result", "highlights", "list"], right: ["band"], collapsed: [] };
  const out = D.arrange(saved, RIDE, all(RIDE));
  assert.deepStrictEqual(out.left, ["upload", "planner", "result", "profiles", "highlights", "weather", "list", "quality", "compare"]);
  assert.deepStrictEqual(out.right, ["band"]);
});

test("verschobene Karten bleiben, wo sie gemerkt sind; neue ueberspringen Nachbarn auf der anderen Seite", () => {
  const saved = { left: ["list", "upload", "planner", "result"], right: ["band", "highlights"], collapsed: ["list"] };
  const out = D.arrange(saved, RIDE, all(RIDE));
  // profiles: naechste bekannte nach ihr waere highlights (rechts) -> weiter bis list (links) -> davor;
  // quality/compare: keine bekannte Nachbarin links mehr dahinter -> ans Ende
  assert.deepStrictEqual(out.left, ["profiles", "weather", "list", "upload", "planner", "result", "quality", "compare"]);
  assert.deepStrictEqual(out.right, ["band", "highlights"]);
  assert.deepStrictEqual(out.collapsed, ["list"]);
});

test("unbekannte, doppelte und fehlende Karten fliegen raus", () => {
  const saved = { left: ["boese", "upload", "upload", "band"], right: ["band", "planner"], collapsed: ["boese", "planner"] };
  const out = D.arrange(saved, RIDE, ["upload", "planner", "band"]);
  assert.deepStrictEqual(out, { left: ["upload", "band"], right: ["planner"], collapsed: ["planner"] });
});

test("kaputtes Gemerktes (kein Objekt, keine Listen) -> Standard", () => {
  for (const bad of ["x", 3, { left: "upload" }, { right: null }]) {
    assert.deepStrictEqual(D.arrange(bad, RIG, all(RIG)), RIG);
  }
});

test("isDefault erkennt die Standard-Anordnung", () => {
  assert.ok(D.isDefault(RIDE, RIDE));
  assert.ok(!D.isDefault({ ...RIDE, collapsed: ["list"] }, RIDE));
  assert.ok(!D.isDefault({ left: RIDE.left.slice(1), right: ["band", "upload"], collapsed: [] }, RIDE));
});

test("newer: der juengere Stand gewinnt, ohne Zeitstempel = aelter", () => {
  const a = { left: [], at: 5 }, b = { left: [], at: 9 };
  assert.strictEqual(D.newer(a, b), b);
  assert.strictEqual(D.newer(b, a), b);
  assert.strictEqual(D.newer(null, a), a);
  assert.strictEqual(D.newer(a, null), a);
  assert.strictEqual(D.newer({ left: [] }, a), a);
  assert.strictEqual(D.newer(null, null), null);
});
