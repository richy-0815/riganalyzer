// node --test tests/js/  –  Umweltzonen: Ableitung und Urteil
const test = require("node:test");
const assert = require("node:assert");
const L = require("../../app/static-rig/lez.js");

test("derive Tabelle", () => {
  const d = (fuel, euro) => L.derive({ fuel, euro });
  const cases = [
    ["diesel", 4, "green", 3, "B"], ["diesel", 3, "yellow", 4, null], ["diesel", 2, "red", 5, null],
    ["diesel", 6, "green", 2, "C"], ["diesel", 5, "green", 2, "B"], ["diesel", 1, null, null, null],
    ["petrol", 4, "green", 2, "C"], ["petrol", 3, "green", 3, "B"], ["petrol", 2, "green", 3, null],
    ["petrol", 1, "green", null, null], ["petrol", 5, "green", 1, "C"],
    ["gas", 4, "green", 1, "ECO"], ["electric", 0, "green", 0, "0"],
  ];
  for (const [f, e, s, c, t] of cases)
    assert.deepStrictEqual(d(f, e), { sticker: s, critair: c, etiqueta: t }, `${f} Euro ${e}`);
  assert.deepStrictEqual(d("diesel", 0), { sticker: null, critair: null, etiqueta: null });
});

const veh = (o) => Object.assign({ fuel: "diesel", euro: 6, zgg: 3.5, critair_vignette: true, lez_registered: false }, o);
const zone = (rule, o) => Object.assign({ id: "z", name: "Z", rule, since: null, until: null }, o);
const W = "2026-06-01";

const T = "2026-10-31";   // todayIso, immer explizit
const V = (z, v, w, t) => L.verdict(z, v, w, t || T);
const out = (r) => [r.v, r.text, r.need];
const crit = (o, zo) => zone(Object.assign({ kind: "critair", vp: 3, pl: 2, vignette: true, hours: null }, o), zo);

test("critair: ok, Vignette fehlt, Klasse zu schlecht, pl, vp null", () => {
  assert.deepStrictEqual(out(V(crit(), veh(), W)), ["ok", "ihr habt Crit'Air 2 ✓", null]);
  assert.deepStrictEqual(out(V(crit(), veh({ critair_vignette: false }), W)), ["no", "Crit'Air-Vignette nötig", "Crit'Air-Vignette"]);
  assert.deepStrictEqual(out(V(crit(), veh({ euro: 3 }), W)), ["no", "Crit'Air 3 oder besser nötig, ihr habt Crit'Air 4", null]);
  assert.deepStrictEqual(out(V(crit(), veh({ euro: 1 }), W)), ["no", "Crit'Air 3 oder besser nötig, ihr habt keine Crit'Air-Klasse", null]);
  assert.strictEqual(V(crit(), veh({ zgg: 4.5, euro: 5 }), W).v, "ok");
  assert.strictEqual(V(crit(), veh({ zgg: 4.5, euro: 4 }), W).v, "no");
  const free = crit({ vp: null });
  assert.deepStrictEqual(out(V(free, veh({ euro: 2 }), W)), ["ok", "keine Einschränkung für euer Fahrzeug ✓", null]);
  assert.strictEqual(V(crit({ pl: null }), veh({ zgg: 7.5, euro: 2 }), W).v, "ok");
});

test("critair: zgg fehlt -> vp", () => {
  const z = crit({ vp: 3, pl: 1 });
  assert.strictEqual(V(z, veh({ zgg: undefined }), W).v, "ok");   // vp 3, ihr Crit'Air 2
  assert.strictEqual(V(z, veh({ zgg: 3.6 }), W).v, "no");         // pl 1
});

test("sticker, etiqueta, euro, unknown: exakte Texte", () => {
  const st = zone({ kind: "sticker", need: "green" });
  assert.deepStrictEqual(out(V(st, veh(), W)), ["ok", "ihr habt die grüne Plakette ✓", null]);
  assert.deepStrictEqual(out(V(st, veh({ euro: 3 }), W)), ["no", "grüne Plakette nötig, ihr habt die gelbe", null]);
  assert.deepStrictEqual(out(V(st, veh({ euro: 1 }), W)), ["no", "grüne Plakette nötig, ihr habt keine", null]);
  const e = zone({ kind: "etiqueta", need: "C" });
  assert.deepStrictEqual(out(V(e, veh(), W)), ["ok", "ihr habt Etikett C ✓", null]);
  assert.deepStrictEqual(out(V(e, veh({ euro: 4 }), W)), ["no", "Etikett C oder besser nötig, ihr habt Etikett B", null]);
  assert.deepStrictEqual(out(V(e, veh({ euro: 2 }), W)), ["no", "Etikett C oder besser nötig, ihr habt keins", null]);
  const reg = zone({ kind: "euro", diesel: 5, petrol: 2, register: true });
  assert.deepStrictEqual(out(V(reg, veh(), W)), ["no", "Registrierung nötig", "Registrierung"]);
  assert.deepStrictEqual(out(V(reg, veh({ lez_registered: true }), W)), ["ok", "Euro-Norm reicht ✓", null]);
  assert.deepStrictEqual(out(V(reg, veh({ euro: 4, lez_registered: true }), W)), ["no", "Euro 5 oder besser nötig, ihr habt Euro 4", null]);
  assert.deepStrictEqual(out(V(zone({ kind: "unknown" }), veh(), W)), ["check", "Regel unbekannt – vor Ort prüfen", null]);
});

test("Euro leer, Gas/Elektro ohne Euro, unbekannter Kraftstoff", () => {
  const st = zone({ kind: "sticker", need: "green" });
  assert.deepStrictEqual(out(V(st, veh({ euro: 0 }), W)), ["check", "Euro-Norm im Fahrzeugprofil eintragen", null]);
  assert.deepStrictEqual(out(V(st, veh({ euro: undefined }), W)), ["check", "Euro-Norm im Fahrzeugprofil eintragen", null]);
  assert.deepStrictEqual(L.derive({ fuel: "gas", euro: 0 }), { sticker: "green", critair: 1, etiqueta: "ECO" });
  assert.deepStrictEqual(out(V(crit(), veh({ fuel: "gas", euro: 0 }), W)), ["ok", "ihr habt Crit'Air 1 ✓", null]);
  assert.deepStrictEqual(out(V(crit(), veh({ fuel: "electric", euro: 0 }), W)), ["ok", "ihr habt Crit'Air 0 ✓", null]);
  assert.deepStrictEqual(L.derive({ fuel: "hydrogen", euro: 6 }), { sticker: null, critair: null, etiqueta: null });
  assert.deepStrictEqual(out(V(st, veh({ fuel: "hydrogen" }), W)), ["check", "Kraftstoff im Fahrzeugprofil prüfen", null]);
});

test("hours (R10): nicht auswerten", () => {
  const h = crit({ hours: "7-19" });
  assert.deepStrictEqual(out(V(h, veh(), W)), ["ok", "ihr habt Crit'Air 2 ✓", null]);
  assert.deepStrictEqual(out(V(h, veh({ euro: 3 }), W)), ["check", "zeitlich begrenzt: 7-19 – Zeiten prüfen", null]);
  assert.deepStrictEqual(out(V(h, veh({ euro: 3 }), null)), ["check", "zeitlich begrenzt: 7-19 – Zeiten prüfen", null]);
  assert.deepStrictEqual(out(V(h, veh(), null)), ["ok", "ihr habt Crit'Air 2 ✓", null]);
  assert.strictEqual(V(crit({ hours: "24/7" }), veh({ euro: 3 }), W).v, "no");
  assert.strictEqual(V(crit({ hours: "" }), veh({ euro: 3 }), W).v, "no");
});

test("Zeit (R11)", () => {
  const ok = crit({ vignette: false }, { since: "2027-01-01" });
  const since = { since: "2027-01-01" };
  // vor since mit Zeitpunkt: ok, need bleibt
  assert.deepStrictEqual(out(V(crit({}, since), veh(), "2026-12-30")), ["ok", "ab 01.01.2027: ihr habt Crit'Air 2 ✓", null]);
  assert.deepStrictEqual(out(V(crit({}, since), veh({ euro: 3 }), "2026-12-30")), ["ok", "ab 01.01.2027: Crit'Air 3 oder besser nötig, ihr habt Crit'Air 4", null]);
  assert.deepStrictEqual(out(V(crit({}, since), veh({ critair_vignette: false }), "2026-12-30")), ["ok", "ab 01.01.2027: Crit'Air-Vignette nötig", "Crit'Air-Vignette"]);
  // am since-Tag gilt die Regel
  assert.strictEqual(V(crit({}, since), veh({ euro: 3 }), "2027-01-01").v, "no");
  // ohne Zeitpunkt: since in der Zukunft -> check
  assert.deepStrictEqual(out(V(crit({}, since), veh(), null)), ["check", "ab 01.01.2027: ihr habt Crit'Air 2 ✓", null]);
  assert.strictEqual(V(ok, veh(), null, "2027-01-01").v, "ok");   // heute == since
  assert.strictEqual(V(crit({}, { since: "2026-10-31" }), veh({ euro: 3 }), null).v, "no");
  const until = { until: "2026-12-31" };
  assert.deepStrictEqual(out(V(crit({}, until), veh({ euro: 3 }), null)), ["no", "Crit'Air 3 oder besser nötig, ihr habt Crit'Air 4", null]);
  assert.deepStrictEqual(out(V(crit({}, { until: "2026-10-01" }), veh({ euro: 3 }), null)), ["ok", "galt nur bis 01.10.2026", null]);
  assert.deepStrictEqual(out(V(crit({}, until), veh({ euro: 3 }), "2027-02-01")), ["ok", "galt nur bis 31.12.2026", null]);
  assert.strictEqual(V(crit({}, until), veh({ euro: 3 }), "2026-12-31").v, "no");
});

test("ICON", () => assert.deepStrictEqual(L.ICON, { ok: "🟢", no: "🔴", check: "🟠" }));

test("pickStage", () => {
  const a = { id: "a", name: "Lyon", since: "2025-01-01", until: "2025-12-31" };
  const b = { id: "b", name: "Lyon", since: "2026-01-01", until: null };
  const p = { id: "p", name: "Paris", since: null, until: null };
  const ids = (z, w, t) => L.pickStage(z, w, t || T).map((x) => x.id);
  assert.deepStrictEqual(ids([a, b, p], "2026-06-01"), ["b", "p"]);
  assert.deepStrictEqual(ids([a, b, p], "2025-06-01"), ["a", "p"]);
  assert.deepStrictEqual(ids([a, b], null), ["b"]);
  assert.deepStrictEqual(ids([a, b], null, "2025-03-01"), ["a"]);
  assert.deepStrictEqual(ids([p, { id: "q", name: "Nice" }], null), ["p", "q"]);
  // nur Zukunft -> frueheste; nur abgelaufen -> juengste
  const f1 = { id: "f1", name: "X", since: "2027-01-01" }, f2 = { id: "f2", name: "X", since: "2028-01-01" };
  assert.deepStrictEqual(ids([f2, f1], null), ["f1"]);
  const e1 = { id: "e1", name: "Y", since: "2020-01-01", until: "2021-01-01" }, e2 = { id: "e2", name: "Y", since: "2022-01-01", until: "2023-01-01" };
  assert.deepStrictEqual(ids([e1, e2], null), ["e2"]);
  // Name null -> je id eine Gruppe
  assert.deepStrictEqual(ids([{ id: "n1", name: null }, { id: "n2", name: null }], null), ["n1", "n2"]);
  // ueberlappend -> erster Treffer in Listenreihenfolge
  const o1 = { id: "o1", name: "Z", since: "2026-01-01", until: "2026-12-31" }, o2 = { id: "o2", name: "Z", since: "2026-06-01", until: null };
  assert.deepStrictEqual(ids([o1, o2], "2026-07-01"), ["o1"]);
  assert.deepStrictEqual(ids([o2, o1], "2026-07-01"), ["o2"]);
});

test("pickStage: Name + Gebiet (R18)", () => {
  const sq = (lat, lon) => [[[lat, lon], [lat, lon + 0.05], [lat + 0.05, lon + 0.05], [lat + 0.05, lon], [lat, lon]]];
  const ids = (z, w) => L.pickStage(z, w, T).map((x) => x.id);
  // gleicher Name, verschiedene Gebiete (Gemeinden) -> beide bleiben
  const s1 = { id: "s1", name: "Strasbourg", since: "2028-01-01", poly: sq(48.5, 7.7) };
  const s2 = { id: "s2", name: "Strasbourg", since: "2028-01-01", poly: sq(48.6, 7.8) };
  assert.deepStrictEqual(ids([s1, s2], "2028-06-01"), ["s1", "s2"]);
  // gleicher Name, gleiches Gebiet (leicht andere Ringe, gleicher gerundeter Rahmen) -> Stufe nach Datum
  const a = { id: "a", name: "Lyon", since: "2025-01-01", until: "2025-12-31", poly: sq(45.7, 4.8) };
  const b = { id: "b", name: "Lyon", since: "2026-01-01", poly: sq(45.7, 4.8) };
  assert.deepStrictEqual(ids([a, b], "2025-06-01"), ["a"]);
  assert.deepStrictEqual(ids([a, b], "2026-06-01"), ["b"]);
  // Teilzonen mit eigenen Stufen: je Gebiet die gueltige
  const a2 = { ...s1, id: "t1", since: "2027-01-01", until: "2027-12-31" };
  assert.deepStrictEqual(ids([a2, s1, s2], "2027-06-01"), ["t1", "s2"]);
});
