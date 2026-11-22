// node --test tests/js/  –  Autobahn-Piktogramm und Umschalter im Routenplaner
const test = require("node:test");
const assert = require("node:assert");
const A = require("../../app/static/autobahn.js");

test("Piktogramm: eigenes Inline-SVG, blaues Schild, ohne externe Datei", () => {
  const s = A.pic();
  assert.match(s, /^<span class="ab-pic" aria-hidden="true"><svg /);
  assert.match(s, /viewBox="0 0 24 24"/);
  assert.doesNotMatch(s, /href=|src=|url\(/);
  assert.match(A.pic(true), /^<span class="ab-pic off"/);
});

test("Zustand aller Abschnitte: mit / ohne / gemischt", () => {
  assert.strictEqual(A.state(["fast", "fast"], "fast"), "on");
  assert.strictEqual(A.state(["noway"], "fast"), "off");
  assert.strictEqual(A.state(["fast", "noway"], "fast"), "mixed");
  // noch keine Abschnitte: Standard-Streckenart entscheidet
  assert.strictEqual(A.state([], "noway"), "off");
  assert.strictEqual(A.state([], "fast"), "on");
});

test("Umschalten: mit <-> ohne, gemischt wird zu mit", () => {
  assert.strictEqual(A.next("fast"), "noway");
  assert.strictEqual(A.next("noway"), "fast");
  assert.strictEqual(A.nextAll("on"), "noway");
  assert.strictEqual(A.nextAll("off"), "fast");
  assert.strictEqual(A.nextAll("mixed"), "fast");
});

test("Abschnitts-Chip (Rig): ein Knopf, Ziel-Modus im data-mode, Zustand gruen/rot", () => {
  const on = A.legChip(2, "fast", "Abschnitt bis Lyon");
  assert.match(on, /data-leg="2"/);
  assert.match(on, /data-mode="noway"/);          // Klick schaltet auf „ohne“
  assert.match(on, /data-state="on"/);
  assert.match(on, /role="switch"/);
  assert.match(on, /aria-checked="true"/);
  assert.match(on, />mit</);
  const off = A.legChip(1, "noway", "x");
  assert.match(off, /data-mode="fast"/);
  assert.match(off, /data-state="off"/);
  assert.match(off, /aria-checked="false"/);
  assert.match(off, />ohne</);
});

test("Beschriftung der Ride-Knöpfe: Piktogramm, durchgestrichen + ohne, kurvig", () => {
  assert.match(A.legLabel("fast"), /class="ab-pic"/);
  assert.match(A.legLabel("noway"), /class="ab-pic off".*ohne$/);
  assert.strictEqual(A.legLabel("curvy"), "kurvig");
  assert.strictEqual(A.TEXT.mixed, "gemischt");
});
