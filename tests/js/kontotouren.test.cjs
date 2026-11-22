// node --test tests/js/  –  „Meine Touren“ im Konto: Paket-Zeile, Summe, Kopfzeile je Tour
const test = require("node:test");
const assert = require("node:assert");
const K = require("../../app/static-rig/konto-touren.js");

test("pkgLine je Zustand", () => {
  assert.match(K.pkgLine({ state: "ready", size: 1.4e9, built: "2026-11-06T22:04:00" }), /^📱 Navi-Paket bereit · 1,4 GB · Stand 06\.11\. 22:04$/);
  assert.equal(K.pkgLine({ state: "building", progress: 40 }), "📱 Navi-Paket wird gebaut · 40 %");
  assert.equal(K.pkgLine({ state: "queued", progress: 0 }), "📱 Navi-Paket wartet auf den Bau");
  assert.equal(K.pkgLine({ state: "error", msg: "Kein Platz" }), "⚠ Navi-Paket fehlgeschlagen: Kein Platz");
  assert.equal(K.pkgLine({ state: "none" }), "kein Navi-Paket");
  assert.equal(K.pkgLine(null), "kein Navi-Paket");
});

test("hasPackage nur bei bereit", () => {
  assert.equal(K.hasPackage({ package: { state: "ready" } }), true);
  assert.equal(K.hasPackage({ package: { state: "building" } }), false);
  assert.equal(K.hasPackage({}), false);
});

test("summary zählt Touren und bereite Pakete", () => {
  const list = [{ package: { state: "ready", size: 1.4e9 } }, { package: { state: "ready", size: 0.6e9 } }, { package: { state: "none" } }];
  assert.equal(K.summary(list), "3 Touren · Navi-Pakete 2 GB");
  assert.equal(K.summary([{ package: { state: "none" } }]), "1 Tour · keine Navi-Pakete");
  assert.equal(K.summary([]), "noch keine Touren");
});

test("meta: Strecke, Etappen, Datum", () => {
  assert.equal(K.meta({ from: "Berlin", to: "Castellane", stages: 9, updated: "2026-11-06T21:00:00" }), "Berlin → Castellane · 9 Etappen · 06.11.26");
  assert.equal(K.meta({ from: "A", to: "B", stages: 1, updated: "" }), "A → B · 1 Etappe");
});
