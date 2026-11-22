// node --test tests/js/  –  Layout-Hilfen: Ebenen merken, Profil-Standard, Naechte
const test = require("node:test");
const assert = require("node:assert");
const L = require("../../app/static/layout.js");

const mem = () => { const m = {}; return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); } }; };
const broken = { getItem: () => { throw new Error("gesperrt"); }, setItem: () => { throw new Error("gesperrt"); } };

test("loadLayersDefaultAndSaved", () => {
  const s = mem();
  assert.deepStrictEqual(L.loadLayers(s, "rig.layers", ["issues", "stops"]), ["issues", "stops"]);
  L.saveLayers(s, "rig.layers", ["camp"]);
  assert.deepStrictEqual(L.loadLayers(s, "rig.layers", ["issues"]), ["camp"]);
});

test("brokenStorageUsesDefaults", () => {
  assert.deepStrictEqual(L.loadLayers(broken, "k", ["a"]), ["a"]);
  const s = mem(); s.setItem("k", "{kaputt");
  assert.deepStrictEqual(L.loadLayers(s, "k", ["a"]), ["a"]);
  assert.doesNotThrow(() => L.saveLayers(broken, "k", ["x"]));
  assert.deepStrictEqual(L.loadLayers(null, "k", ["a"]), ["a"]);
});

test("profileOpenRules", () => {
  assert.strictEqual(L.profileOpen(mem(), "rig.profile", 900), true);
  assert.strictEqual(L.profileOpen(mem(), "rig.profile", 800), false);
  const s = mem(); s.setItem("rig.profile", "0");
  assert.strictEqual(L.profileOpen(s, "rig.profile", 900), false);
  assert.strictEqual(L.profileOpen(broken, "rig.profile", 900), true);
});

test("nightsStepClamp", () => {
  assert.strictEqual(L.nightsStep(0, -1), 0);
  assert.strictEqual(L.nightsStep(30, 1), 30);
  assert.strictEqual(L.nightsStep("x", 1), 1);
  assert.strictEqual(L.nightsStep(2, 1), 3);
});

test("profileOpenOnPhones", () => {
  // Handy (≤ 900 px breit): Karte hat feste Hoehe, Zuklappen bringt nichts – Profil bleibt offen
  const mem2 = () => { const m = {}; return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); } }; };
  assert.strictEqual(L.profileOpen(mem2(), "rig.profile", 700, 390), true);
  assert.strictEqual(L.profileOpen(mem2(), "rig.profile", 800, 1440), false);
});

test("fitWhenSettled: erst Layout abwarten, dann Groesse neu lesen und mit Rand einpassen (0.15.3)", () => {
  const calls = [], frames = [];
  globalThis.requestAnimationFrame = (f) => frames.push(f);
  const map = { invalidateSize: () => calls.push("size"), fitBounds: (b, o) => calls.push(["fit", b, o]) };
  L.fitWhenSettled(map, "B");
  assert.deepStrictEqual(calls, []);                          // nicht sofort – Profil/Karten kommen noch
  while (frames.length) frames.shift()();
  assert.deepStrictEqual(calls, ["size", ["fit", "B", { padding: [24, 24], animate: false }]]);
  delete globalThis.requestAnimationFrame;
});
