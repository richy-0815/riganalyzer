// Gemeinsame Kachel-Logik: Mein Konto (eine Spalte) und Admin-Dashboard (zwei Spalten, halb/ganz breit).
const test = require("node:test");
const assert = require("node:assert");
const C = require("../../app/static/cards.js");

// Admin-Standard „B: Logins oben“
const DEF = {
  order: ["logins", "aktiv", "rueckmeldungen", "gruppen", "kosten", "aenderungen", "versionen"],
  wide: ["logins", "aenderungen", "versionen"],
  col: { aktiv: "l", rueckmeldungen: "r", gruppen: "l", kosten: "r" },
};
const std = () => C.norm(null, DEF);

test("norm: ohne gemerkte Anordnung gilt der Standard", () => {
  const s = std();
  assert.deepStrictEqual(s.order, DEF.order);
  assert.deepStrictEqual(s.wide, ["logins", "aenderungen", "versionen"]);
  assert.deepStrictEqual(s.collapsed, []);
  assert.strictEqual(s.col.kosten, "r");
  assert.strictEqual(s.col.aktiv, "l");
});

test("norm: gemerkte Breite/Spalte gilt nur fuer Kacheln, die der Stand kennt", () => {
  const saved = { order: ["aktiv", "logins"], wide: ["aktiv"], col: { logins: "r" }, collapsed: ["logins", "boese"] };
  const s = C.norm(saved, DEF);
  assert.ok(s.wide.includes("aktiv"));            // gemerkt: ganz breit
  assert.ok(!s.wide.includes("logins"));          // gemerkt: halb (bekannt, nicht in wide)
  assert.strictEqual(s.col.logins, "r");
  assert.ok(s.wide.includes("aenderungen"));      // unbekannt -> Standard
  assert.strictEqual(s.col.kosten, "r");
  assert.deepStrictEqual(s.collapsed, ["logins"]);
  assert.deepStrictEqual(s.order.slice(0, 2), ["aktiv", "logins"]);
});

test("norm: fehlende Kacheln (Ride ohne Kosten, Gruppen-Admin) stoeren nicht", () => {
  const ride = { order: ["logins", "aktiv", "aenderungen"], wide: ["logins", "aenderungen"], col: { aktiv: "l" } };
  const s = C.norm({ order: DEF.order, wide: DEF.wide, col: DEF.col }, ride);
  assert.deepStrictEqual(s.order, ["logins", "aktiv", "aenderungen"]);
  assert.deepStrictEqual(Object.keys(s.col).sort(), ["aenderungen", "aktiv", "logins"]);
});

test("rows: ganz breite Kacheln trennen, halbe teilen sich links/rechts", () => {
  assert.deepStrictEqual(C.rows(std()), [
    { wide: "logins" },
    { l: ["aktiv", "gruppen"], r: ["rueckmeldungen", "kosten"] },
    { wide: "aenderungen" },
    { wide: "versionen" },
  ]);
});

test("rows: Ride ohne Kosten – rechte Spalte endet frueher", () => {
  const s = C.norm(null, { order: ["logins", "aktiv", "rueckmeldungen", "gruppen", "aenderungen"],
    wide: ["logins", "aenderungen"], col: { aktiv: "l", rueckmeldungen: "r", gruppen: "l" } });
  assert.deepStrictEqual(C.rows(s)[1], { l: ["aktiv", "gruppen"], r: ["rueckmeldungen"] });
});

test("step: halbe Kachel ↑/↓ nur innerhalb ihrer Spalte", () => {
  let s = C.step(std(), "gruppen", -1);
  assert.deepStrictEqual(C.rows(s)[1], { l: ["gruppen", "aktiv"], r: ["rueckmeldungen", "kosten"] });
  s = C.step(s, "gruppen", 1);
  assert.deepStrictEqual(C.rows(s)[1], { l: ["aktiv", "gruppen"], r: ["rueckmeldungen", "kosten"] });
});

test("step: oben in der Spalte springt die halbe Kachel ueber die breite davor", () => {
  const s = C.step(std(), "rueckmeldungen", -1);
  assert.deepStrictEqual(C.rows(s).slice(0, 3), [
    { l: [], r: ["rueckmeldungen"] },
    { wide: "logins" },
    { l: ["aktiv", "gruppen"], r: ["kosten"] },
  ]);
});

test("step: unten in der Spalte springt sie unter die naechste breite", () => {
  const s = C.step(std(), "kosten", 1);
  assert.deepStrictEqual(C.rows(s).slice(1, 4), [
    { l: ["aktiv", "gruppen"], r: ["rueckmeldungen"] },
    { wide: "aenderungen" },
    { l: [], r: ["kosten"] },
  ]);
});

test("step: breite Kachel springt ueber eine ganze Zeile", () => {
  let s = C.step(std(), "aenderungen", -1);
  assert.deepStrictEqual(C.rows(s).slice(0, 3), [
    { wide: "logins" }, { wide: "aenderungen" }, { l: ["aktiv", "gruppen"], r: ["rueckmeldungen", "kosten"] },
  ]);
  s = C.step(s, "aenderungen", 1);
  assert.deepStrictEqual(s.order, std().order);
});

test("step: an den Raendern bleibt alles", () => {
  assert.deepStrictEqual(C.step(std(), "logins", -1).order, std().order);
  assert.deepStrictEqual(C.step(std(), "versionen", 1).order, std().order);
  assert.deepStrictEqual(C.step(std(), "nix", 1).order, std().order);
});

test("toggle: ⇔ breit/halb, ⇄ Spalte, ▾ einklappen – ohne den alten Stand zu aendern", () => {
  const a = std();
  const b = C.toggle(a, "wide", "aktiv");
  assert.ok(b.wide.includes("aktiv") && !a.wide.includes("aktiv"));
  assert.deepStrictEqual(C.rows(b).slice(0, 3), [{ wide: "logins" }, { wide: "aktiv" },
    { l: ["gruppen"], r: ["rueckmeldungen", "kosten"] }]);
  assert.ok(!C.toggle(b, "wide", "aktiv").wide.includes("aktiv"));
  const c = C.toggle(a, "col", "gruppen");
  assert.strictEqual(c.col.gruppen, "r");
  assert.strictEqual(a.col.gruppen, "l");
  assert.deepStrictEqual(C.rows(c)[1], { l: ["aktiv"], r: ["rueckmeldungen", "gruppen", "kosten"] });
  assert.strictEqual(C.toggle(c, "col", "gruppen").col.gruppen, "l");
  const d = C.toggle(a, "collapsed", "kosten");
  assert.deepStrictEqual(d.collapsed, ["kosten"]);
  assert.deepStrictEqual(C.toggle(d, "collapsed", "kosten").collapsed, []);
});

test("place: Ziehen vor/hinter eine Kachel, halbe uebernimmt deren Spalte", () => {
  let s = C.place(std(), "kosten", "aktiv", false, "l");
  assert.deepStrictEqual(C.rows(s)[1], { l: ["kosten", "aktiv", "gruppen"], r: ["rueckmeldungen"] });
  s = C.place(std(), "aenderungen", "aktiv", true);   // breite Kachel teilt die Zeile
  assert.deepStrictEqual(C.rows(s).slice(1, 4), [{ l: ["aktiv"], r: [] }, { wide: "aenderungen" },
    { l: ["gruppen"], r: ["rueckmeldungen", "kosten"] }]);
  assert.strictEqual(s.col.aenderungen, std().col.aenderungen);   // breite behaelt ihre Spalte
  s = C.place(std(), "logins", null, true);             // ans Ende
  assert.strictEqual(s.order[s.order.length - 1], "logins");
  assert.deepStrictEqual(C.place(std(), "aktiv", "aktiv", true).order, std().order);
});

test("snapshot: gespeichert werden order/collapsed/wide/col ohne Laufzeitfelder", () => {
  const s = C.snapshot(C.toggle(std(), "collapsed", "aktiv"));
  assert.deepStrictEqual(Object.keys(s).sort(), ["col", "collapsed", "order", "wide"]);
  assert.deepStrictEqual(C.snapshot(std(), false), { order: std().order, collapsed: [] });   // Konto: eine Spalte
});

test("arrange/move/newer bleiben wie bei Mein Konto", () => {
  assert.deepStrictEqual(C.arrange(["b", "x"], ["a", "b"]), ["a", "b"]);
  assert.deepStrictEqual(C.move(["a", "b", "c"], "b", -1), ["b", "a", "c"]);
  assert.strictEqual(C.newer({ at: 5 }, { at: 3 }).at, 5);
  assert.strictEqual(C.newer(null, null), null);
});

test("Standard eingeklappt (data-fold) gilt nur fuer Kacheln, die der gemerkte Stand nicht kennt", () => {
  const defs = { order: ["a", "b", "v"], wide: [], col: {}, collapsed: ["v"] };
  assert.deepStrictEqual(C.norm(null, defs).collapsed, ["v"]);                        // frisch: eingeklappt
  assert.deepStrictEqual(C.norm({ order: ["a", "b"], collapsed: [] }, defs).collapsed, ["v"]);   // neu dazugekommen
  assert.deepStrictEqual(C.norm({ order: ["a", "b", "v"], collapsed: [] }, defs).collapsed, []); // selbst aufgeklappt
});
