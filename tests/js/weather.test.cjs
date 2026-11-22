// node --test tests/js/  –  Wetter-Anzeige im Fahrplan
const test = require("node:test");
const assert = require("node:assert");
const W = require("../../app/static/weather.js");

const end = { temp: 23.6, precip_mm: 0, wind: 12, gust: 35, code: 1, text: "überwiegend klar" };

test("stripText Werte und Gespann-Ampel", () => {
  const s = W.stripText({ end, worst: { precip_mm: 4.2, precip_at: 120, gust: 65, gust_at: 80, gust_level: "tight" } });
  assert.strictEqual(s.main, "☀ 24° · 🌧 0 mm · 💨 Böen 35 km/h");
  assert.strictEqual(s.sub, "unterwegs: Regen bis 4,2 mm bei km 120 · Böen bis 65 km/h – mit Anhänger vorsichtig");
  assert.strictEqual(s.level, "tight");
  assert.strictEqual(W.stripText({ end, worst: { precip_mm: 0.2, gust: 30, gust_level: null } }).sub, "");
  assert.match(W.stripText({ end, worst: { precip_mm: 0, gust: 85, gust_at: 10, gust_level: "critical" } }).sub, /Fahrt mit Anhänger überdenken/);
});

test("later und Ausfall", () => {
  assert.strictEqual(W.stripText({ later: "ab 25.05. verfügbar", end: null }).main, "🗓 Vorhersage ab 25.05. verfügbar");
  assert.strictEqual(W.stripText({ later: null, end: null }).main, "Wetter gerade nicht abrufbar");
});

test("stagePoints alle 50 km, ETA steigt, letzter = Ziel", () => {
  const line = [[52.5, 13.4, 0], [52.0, 12.0, 60], [51.0, 10.0, 130], [50.5, 8.6, 200]];
  const dep = Date.UTC(2027, 4, 24, 6, 30), arr = Date.UTC(2027, 4, 24, 10, 30);
  const p = W.stagePoints(line, dep, arr, 50);
  assert.deepStrictEqual(p.map((x) => x[3]), [0, 50, 100, 150, 200]);
  assert.strictEqual(p[0][2], "2027-05-24T06:30:00.000Z");
  assert.strictEqual(p[p.length - 1][2], "2027-05-24T10:30:00.000Z");
  assert.deepStrictEqual(p[p.length - 1].slice(0, 2), [50.5, 8.6]);
  for (let i = 1; i < p.length; i++) assert.ok(p[i][2] > p[i - 1][2]);
});

test("warnRows: rot vor orange, Zeitraum und Gebiet", () => {
  const r = W.warnRows([{ level: "orange", event: "Gewitter", from: "2027-05-24T12:00:00Z", to: "2027-05-24T18:00:00Z", area: "Var", source: "MeteoAlarm" },
    { level: "red", event: "Wind", from: "2027-05-24T08:00:00Z", to: "2027-05-24T20:00:00Z", area: "Bouches-du-Rhône", source: "MeteoAlarm" }]);
  assert.deepStrictEqual(r.map((x) => x.cls), ["w-red", "w-orange"]);
  assert.strictEqual(r[1].text, "⚠ Gewitter · 14:00–20:00 · Var");
});

// ---- RideAnalyzer: Abfahrt, Punkte je Tour, Zeilen, Warnungen ohne Doppel
test("Abfahrt-Vorschlag: vormittags heute zur naechsten vollen Stunde, sonst morgen 9 Uhr", () => {
  const W = require("../../app/static/weather.js");
  const loc = (d) => [d.getFullYear(), d.getMonth() + 1, d.getDate(), d.getHours(), d.getMinutes()];
  assert.deepStrictEqual(loc(W.defaultDeparture(new Date(2027, 4, 24, 7, 20))), [2027, 5, 24, 8, 0]);
  assert.deepStrictEqual(loc(W.defaultDeparture(new Date(2027, 4, 24, 13, 5))), [2027, 5, 25, 9, 0]);
  assert.strictEqual(W.localInput(new Date(2027, 4, 24, 8, 0)), "2027-05-24T08:00");
});

test("tourPoints: ein Punkt je Abschnitt als eigene Etappe, Zeit nach km, hoechstens 9", () => {
  const W = require("../../app/static/weather.js");
  const line = [[50, 8, 0], [50.5, 8.5, 100], [51, 9, 200]];
  const pts = W.tourPoints(line, Date.UTC(2027, 4, 24, 8), 4);
  assert.strictEqual(pts.length, 9);
  assert.deepStrictEqual(pts[0], { n: 1, pts: [[50, 8, "2027-05-24T08:00:00.000Z", 0]] });
  assert.deepStrictEqual(pts[8].pts[0], [51, 9, "2027-05-24T12:00:00.000Z", 200]);
  assert.strictEqual(pts[4].pts[0][2], "2027-05-24T10:00:00.000Z");
});

test("pointRow und Warnungen ohne Doppel", () => {
  const W = require("../../app/static/weather.js");
  const st = { n: 2, end: { code: 61, temp: 14.4, precip_mm: 1.2, gust: 64 }, worst: { gust_level: "info" },
    warnings: [{ level: "orange", event: "Gewitter", from: "2027-05-24T10:00:00Z", to: "2027-05-24T20:00:00Z", area: "Gießen", source: "DWD" }] };
  const row = W.pointRow(st, { km: 25, iso: "2027-05-24T10:00:00Z" });
  assert.match(row.text, /🌧 14° · 1,2 mm · 💨 64 km\/h/);
  assert.strictEqual(row.level, "info");
  assert.strictEqual(row.km, 25);
  assert.strictEqual(W.pointRow({ later: "ab 25.05. verfügbar" }, { km: 0, iso: "" }).text, "🗓 Vorhersage ab 25.05. verfügbar");
  assert.strictEqual(W.uniqueWarnings([st, { warnings: st.warnings }, { warnings: [] }]).length, 1);
});
