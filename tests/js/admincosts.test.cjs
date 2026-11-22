// node --test tests/js/  –  Admin „Kosten“: Balken, Skala, Euro-Format
const test = require("node:test");
const assert = require("node:assert");
const C = require("../../app/static/admin-costs.js");

const near = (a, b) => assert.ok(Math.abs(a - b) < 0.01, `${a} != ${b}`);
const acc = (o) => ({ name: "x", active: true, variable: 0, cost: 0, profit_abo: 0, price_payg: 0, share: 0, ...o });

test("Gewinn: Verbrauch, Anteil, Gewinn", () => {
  const [b] = C.bars([acc({ variable: 0.5, share: 1.5, cost: 2, profit_abo: 2.67, price_payg: 2.5 })], 5);
  assert.deepStrictEqual(b.segs.map((s) => s.kind), ["var", "share", "profit"]);
  near(b.segs[0].from, 0); near(b.segs[0].to, 10);
  near(b.segs[1].from, 10); near(b.segs[1].to, 40);
  near(b.segs[2].from, 40); near(b.segs[2].to, 93.4);
  near(b.marker, 50);
});

test("Verlust: loss-Segment, kein profit", () => {
  const [b] = C.bars([acc({ variable: 1, share: 5, cost: 6, profit_abo: -1.33, price_payg: 7 })], 10);
  assert.ok(!b.segs.some((s) => s.kind === "profit"));
  const l = b.segs.find((s) => s.kind === "loss");
  near(l.from, 46.7); near(l.to, 60);
});

test("inaktiv: keine Segmente, kein Marker", () => {
  const [b] = C.bars([acc({ active: false })], 5);
  assert.deepStrictEqual(b.segs, []);
  assert.strictEqual(b.marker, null);
});

test("scale: max ueber Kosten+Gewinn, mindestens Abo", () => {
  const sc = { accounts: [acc({ cost: 2, profit_abo: 2.67 }), acc({ cost: 6, profit_abo: -1.33 })] };
  near(C.scale(sc, 5), 6);
  near(C.scale({ accounts: [acc({ cost: 1, profit_abo: 1 })] }, 5), 5);
  near(C.scale({ accounts: [] }, 5), 5);
});

test("eur", () => {
  assert.strictEqual(C.eur(1234.5), "1.234,50 €");
  assert.strictEqual(C.eur(1.234), "1,23 €");
  assert.strictEqual(C.eur(-1.33), "-1,33 €");
});

test("Summenzeile: Spannen ergeben alle Spalten, Summen unter eigener Ueberschrift", () => {
  const cells = C.summaryCells({ cost: 10, profit_abo: -2, profit_payg: 3 });
  assert.strictEqual(cells.reduce((n, c) => n + c.span, 0), C.COLS);
  assert.strictEqual(C.COLS, 12);
  // Spalte 9 = Kosten, 10 = Gewinn mit Abo, 11 = Preis o. Abo (leer), 12 = Gewinn o. Abo
  let col = 1; const at = {};
  for (const c of cells) { at[col] = c.text; col += c.span; }
  assert.strictEqual(at[9], "10,00 €"); assert.strictEqual(at[10], "-2,00 €"); assert.strictEqual(at[11], ""); assert.strictEqual(at[12], "3,00 €");
});

test("scale: Preis ohne Abo liegt immer in der Skala (Marker nicht abgeschnitten)", () => {
  const a = acc({ cost: 6.33, profit_abo: -1, price_payg: 8.6 });
  near(C.scale({ accounts: [a] }, 5), 8.6);
  near(C.bars([a], C.scale({ accounts: [a] }, 5))[0].marker, 100);
  near(C.scale({ accounts: [acc({ active: false, price_payg: 99 })] }, 5), 5);
});

test("priceBody: leere Zahlenfelder werden nicht gesendet", () => {
  const b = C.priceBody([{ name: "a", type: "number", value: "" }, { name: "b", type: "number", value: "2,5".replace(",", ".") },
    { name: "c", type: "text", value: "x" }, { name: "d", type: "number", value: "  " }]);
  assert.deepStrictEqual(b, { b: 2.5, c: "x" });
});

test("breakEvenText: nicht erreichbar bei null", () => {
  assert.strictEqual(C.breakEvenText({ break_even: null }), "Gewinnschwelle: nicht erreichbar.");
  assert.strictEqual(C.breakEvenText({ break_even: 3 }), "Gewinnschwelle: 3 zahlende Accounts.");
});

// Entwicklung (Claude) je Login
test("devRows: nach Euro absteigend, Euro formatiert, Tickets mit App und Status", () => {
  const dev = { stand: "2026-11-09T12:00:00", logins: [
    { login: "olaf", usd: 1, eur: 0.92, tickets: [{ nr: 20, titel: "Idee von olaf: Brücken", status: "offen", app: "rig", usd: 1, eur: 0.92 }] },
    { login: "otto", usd: 3, eur: 2.76, tickets: [{ nr: 22, titel: "Fehler von otto: X", status: "erledigt", app: "ride", usd: 3, eur: 2.76 }] }] };
  const rows = C.devRows(dev);
  assert.deepStrictEqual(rows.map((r) => r.login), ["otto", "olaf"]);
  assert.equal(rows[0].eur, C.eur(2.76));
  assert.equal(rows[1].tickets[0].text, "#20 · Rig · offen · Idee von olaf: Brücken");
  assert.equal(rows[1].tickets[0].eur, C.eur(0.92));
});
test("devRows: ohne Abgleich oder ohne Läufe leer", () => {
  assert.deepStrictEqual(C.devRows(null), []);
  assert.deepStrictEqual(C.devRows({ stand: "x", logins: [] }), []);
});
