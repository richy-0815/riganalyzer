// node --test tests/js/  –  Text-Helfer des Routenplaners (plantext.js)
const test = require("node:test");
const assert = require("node:assert");
const P = require("../../app/static/plantext.js");

test("Ticket #6: Zeitunterschied ab 60 min als Stunden", () => {
  assert.strictEqual(P.signedTime(654), "+10:54 h");
  assert.strictEqual(P.signedTime(60), "+1:00 h");
  assert.strictEqual(P.signedTime(-75), "−1:15 h");
});

test("Ticket #6: unter 60 min weiter in Minuten, 0 als ±", () => {
  assert.strictEqual(P.signedTime(25), "+25 min");
  assert.strictEqual(P.signedTime(-59), "−59 min");
  assert.strictEqual(P.signedTime(0), "± 0 min".replace(" ", ""));
});

const pt = (o = {}) => ({ lat: 50.57981, lon: 8.64562, name: "", stop: false, ...o });

test("Ticket #9: Umbenennen setzt name + custom, leer -> wieder Koordinate", () => {
  const p = P.rename(pt({ name: "bei Gießen", auto: true }), "  Wetzlar   Dom  ");
  assert.deepStrictEqual([p.name, p.custom, p.auto], ["Wetzlar Dom", true, undefined]);
  P.rename(p, "   ");
  assert.deepStrictEqual([p.name, p.custom], ["", undefined]);
  assert.strictEqual(P.label(p), "50.5798, 8.6456");
});

test("Ticket #9: höchstens 60 Zeichen, HTML bleibt Text", () => {
  assert.strictEqual(P.rename(pt(), "x".repeat(80)).name.length, 60);
  assert.strictEqual(P.rename(pt(), "<b>Hof</b>").name, "<b>Hof</b>");   // Anzeige escaped (planner.js esc)
});

test("Ticket #9: Verschieben behält eigenen Namen, verwirft Vorschlag und Suchtreffer", () => {
  assert.strictEqual(P.afterMove(pt({ name: "Mein Platz", custom: true })).name, "Mein Platz");
  const auto = P.afterMove(pt({ name: "bei Gießen", auto: true }));
  assert.deepStrictEqual([auto.name, auto.auto], ["", undefined]);
  assert.strictEqual(P.afterMove(pt({ name: "Wetzlar, Hessen" })).name, "");
});

test("Ticket #9: Vorschlag nie über eigenen oder vorhandenen Namen", () => {
  const p = pt();
  assert.strictEqual(P.applySuggestion(p, "Wetzlar"), true);
  assert.deepStrictEqual([p.name, p.auto], ["Wetzlar", true]);
  assert.strictEqual(P.applySuggestion(pt({ name: "Eigen", custom: true }), "Wetzlar"), false);
  assert.strictEqual(P.applySuggestion(pt({ name: "Treffer" }), "Wetzlar"), false);
  assert.strictEqual(P.applySuggestion(pt(), ""), false);
});

test("Ticket #9: Navi-Name – Suchtreffer bis zum Komma, eigene Namen ganz", () => {
  assert.strictEqual(P.stopName(pt({ name: "Wetzlar, Lahn-Dill-Kreis, Hessen" })), "Wetzlar");
  assert.strictEqual(P.stopName(pt({ name: "Wetzlar, Dom", custom: true })), "Wetzlar, Dom");
  assert.strictEqual(P.stopName(pt()), "");
});
