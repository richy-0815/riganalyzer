// node --test tests/js/  –  Navi-Paket-Status: Balken, Schritte, Laufzeit
const test = require("node:test");
const assert = require("node:assert");
const N = require("../../app/static-rig/navistatus.js");

const steps = N.FALLBACK_STEPS;

test("dur: Sekunden, Minuten, Stunden", () => {
  assert.equal(N.dur(45), "45 s");
  assert.equal(N.dur(80), "1:20 Min.");
  assert.equal(N.dur(3900), "1 Std. 05 Min.");
  assert.equal(N.dur(-3), "0 s");
});

test("eta erst ab 20 % und 30 s", () => {
  assert.equal(N.eta(10, 40), null);
  assert.equal(N.eta(120, 5), null);
  assert.equal(N.eta(120, 40), "noch ca. 3 Min.");
  assert.equal(N.eta(3600, 40), "noch ca. 1,5 Std.");
  assert.equal(N.eta(30, 70), "noch ca. 15 s");
  assert.equal(N.eta(100, 100), null);
});

test("building: Häkchen bis zum aktuellen Schritt, Prozent, Laufzeit", () => {
  const v = N.view({ state: "building", progress: 40, step: 2, steps }, 80);
  assert.equal(v.pct, 40);
  assert.deepEqual(v.steps.map((s) => s.state), ["done", "done", "now", "todo", "todo"]);
  assert.equal(v.steps[0].label, "Route berechnet");
  assert.equal(v.steps[2].label, "Karte wird geladen");
  assert.equal(v.time, "läuft seit 1:20 Min.");
  assert.equal(v.eta, "noch ca. 2 Min.");
});

test("rest: Sekunden unter einer Minute, gleich fertig (0.51.1)", () => {
  assert.equal(N.rest(5), "gleich fertig");
  assert.equal(N.rest(-4), "gleich fertig");
  assert.equal(N.rest(23), "noch ca. 25 s");
  assert.equal(N.rest(60), "noch ca. 1 Min.");
  assert.equal(N.rest(61), "noch ca. 2 Min.");
  assert.equal(N.rest(null), null);
});

test("Server-Restzeit hat Vorrang und zählt seit der Antwort herunter", () => {
  const p = { state: "building", progress: 40, step: 2, steps, eta: 30 };
  assert.equal(N.view(p, 20, 0).eta, "noch ca. 30 s");
  assert.equal(N.view(p, 32, 12).eta, "noch ca. 20 s");
  assert.equal(N.view(p, 50, 25).eta, "gleich fertig");
  assert.equal(N.view({ ...p, eta: null }, 20, 0).eta, null);   // ohne Server-Wert und unter 30 s: nichts
});

test("building ohne Schrittliste vom Server nimmt die eingebaute", () => {
  const v = N.view({ state: "building", progress: 5, step: 0 }, null);
  assert.equal(v.steps.length, 5);
  assert.equal(v.time, "");
});

test("queued: Warteplatz", () => {
  assert.equal(N.view({ state: "queued", ahead: 2, waiting: 30, steps }).sub, "2 Pakete sind vor dir dran.");
  assert.equal(N.view({ state: "queued", ahead: 1, steps }).sub, "Ein Paket ist vor dir dran.");
  assert.equal(N.view({ state: "queued", ahead: 0, steps }).sub, "Gleich geht's los.");
  assert.equal(N.view({ state: "queued", waiting: 30, steps }).time, "wartet seit 30 s");
});

test("ready und none", () => {
  const r = N.view({ state: "ready", size: 1.4e9, took: 200 });
  assert.equal(r.title, "📱 Navi-Paket bereit · 1,4 GB");
  assert.equal(r.time, "gebaut in 3:20 Min.");
  assert.equal(r.pct, 100);
  assert.equal(N.view({ state: "none" }).title, "kein Navi-Paket");
  assert.equal(N.view(null).title, "kein Navi-Paket");
});

test("error: Meldung und fehlgeschlagener Schritt", () => {
  const v = N.view({ state: "error", msg: "Kein Platz", step: 1, steps });
  assert.equal(v.sub, "Kein Platz");
  assert.deepEqual(v.steps.map((s) => s.state), ["done", "fail", "todo", "todo", "todo"]);
  assert.deepEqual(N.view({ state: "error", msg: "x" }).steps, []);
});
