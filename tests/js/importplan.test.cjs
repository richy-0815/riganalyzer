// node --test tests/js/  –  Eckdaten aus einem GPX-Import fuer den Routenplaner
const test = require("node:test");
const assert = require("node:assert");
const P = require("../../app/static-rig/importplan.js");

// Strecke nach Osten entlang 48° N, ein Punkt je ~0,75 km (0,01° Laenge)
const line = (n, lat = 48, lon0 = 10) => Array.from({ length: n }, (_x, i) => [lat, lon0 + i * 0.01]);

test("kurze Strecke: nur Start und Ziel", () => {
  const r = P.pick(line(40));   // ~29 km
  assert.strictEqual(r.points.length, 2);
  assert.strictEqual(r.source, "none");
  assert.deepStrictEqual([r.points[0].lon, r.points[1].lon], [10, 10.39]);
});

test("lange Strecke: Zwischenpunkte alle 50–100 km mit Fahrtrichtung Osten", () => {
  const r = P.pick(line(800));  // ~596 km -> Abstand 50 km -> 11 Zwischenpunkte
  assert.strictEqual(r.source, "auto");
  const vias = r.points.slice(1, -1);
  assert.ok(vias.length >= 9 && vias.length <= 12, `vias=${vias.length}`);
  vias.forEach((v) => { assert.ok(Math.abs(v.heading - 90) <= 1, `heading ${v.heading}`); assert.strictEqual(v.stop, false); });
  assert.ok(r.points.every((p, k) => !k || p.lon > r.points[k - 1].lon), "Reihenfolge entlang der Strecke");
});

test("nie mehr als 25 Punkte, auch bei sehr langer Reise", () => {
  const r = P.pick(line(6000));   // ~4470 km
  assert.ok(r.points.length <= 25);
  assert.ok(r.points.length >= 20);
});

test("Etappen-Enden werden Nacht-Punkte, Start/Ziel nie", () => {
  const r = P.pick(line(300), { stageEnds: [100, 200] });
  const stops = r.points.filter((p) => p.stop);
  assert.deepStrictEqual(stops.map((p) => p.lon), [11, 12]);
  assert.strictEqual(r.points[0].stop, false);
  assert.strictEqual(r.points[r.points.length - 1].stop, false);
});

test("GPX-Wegpunkte auf der Strecke haben Vorrang, fremde und doppelte fallen raus", () => {
  const wpts = [
    { lat: 48.003, lon: 10.8, name: "Pass" },           // ~0,3 km neben der Strecke
    { lat: 48, lon: 10.5, name: "Dorf" },
    { lat: 48, lon: 10.505, name: "Dorf doppelt" },     // < 2 km hinter "Dorf"
    { lat: 49, lon: 10.6, name: "weit weg" },
    { lat: 48, lon: 10, name: "Daheim" },               // am Start -> nur Name
  ];
  const r = P.pick(line(200), { wpts });
  assert.strictEqual(r.source, "wpt");
  assert.deepStrictEqual(r.points.map((p) => p.name), ["Daheim", "Dorf", "Pass", ""]);
});

test("zu viele Wegpunkte werden gleichmaessig ausgeduennt", () => {
  const wpts = Array.from({ length: 60 }, (_x, k) => ({ lat: 48, lon: 10.05 + k * 0.05, name: `W${k}` }));
  const r = P.pick(line(400), { wpts });
  assert.strictEqual(r.points.length, 25);
  assert.ok(r.cut);
});

test("Strecke aus einem Analyse-Ergebnis – einzeln und als Reise", () => {
  const one = P.trackFrom({ segments: [{ c: [[1, 1], [1, 2]] }, { c: [[1, 2], [1, 3]] }] });
  assert.deepStrictEqual(one, { track: [[1, 1], [1, 2], [1, 3]], stageEnds: [], places: [] });
  const trip = P.trackFrom({ trip: true, stages: [{ segments: [{ c: [[1, 1], [1, 2]] }] }, { segments: [{ c: [[1, 2], [1, 3], [1, 4]] }] }] });
  assert.deepStrictEqual(trip.track.length, 4);
  assert.deepStrictEqual(trip.stageEnds, [1]);
});

test("leere Strecke", () => {
  assert.deepStrictEqual(P.pick([]).points, []);
  assert.deepStrictEqual(P.trackFrom(null).track, []);
});

// ---- Import in bestehende Planung einfuegen (merge)
const P2 = (lat, lon, name) => ({ lat, lon, name, stop: false });
const potsdam = P2(52.39, 13.06, "Am Pilz"), odenskog = P2(63.18, 14.65, "Odenskog");
const etappe = [P2(55.3753, 13.1569, "1.1 Trelleborg"), P2(57.7089, 11.9746, "1.3 Goeteborg"), P2(59.3793, 13.5036, "1.8 Karlstad")];

test("einfuegen: eigener Start und Ziel bleiben, Etappe dazwischen in Fahrtrichtung", () => {
  const r = P.merge([potsdam, odenskog], etappe);
  assert.deepStrictEqual(r.points.map((p) => p.name), ["Am Pilz", "1.1 Trelleborg", "1.3 Goeteborg", "1.8 Karlstad", "Odenskog"]);
  assert.strictEqual(r.at, 1);
  assert.strictEqual(r.reversed, false);
  assert.strictEqual(r.added, 3);
});

test("einfuegen: Etappe in Gegenrichtung wird umgedreht, Fahrtrichtung mit", () => {
  const rueck = etappe.slice().reverse().map((p) => ({ ...p, heading: 10 }));
  const r = P.merge([potsdam, odenskog], rueck);
  assert.deepStrictEqual(r.points.map((p) => p.name), ["Am Pilz", "1.1 Trelleborg", "1.3 Goeteborg", "1.8 Karlstad", "Odenskog"]);
  assert.strictEqual(r.reversed, true);
  assert.strictEqual(r.points[1].heading, 190);
});

test("einfuegen: doppelte Punkte fallen weg, Nacht bleibt, Start/Ziel nie Nacht", () => {
  const blk = [P2(52.3901, 13.0601, "fast Potsdam"), { ...P2(57.7089, 11.9746, "Goeteborg"), stop: true }, P2(59.3793, 13.5036, "Karlstad")];
  const r = P.merge([potsdam, odenskog], blk);
  assert.deepStrictEqual(r.points.map((p) => p.name), ["Am Pilz", "Goeteborg", "Karlstad", "Odenskog"]);
  assert.strictEqual(r.points[1].stop, true);
  assert.strictEqual(r.points[0].stop, false);
  assert.strictEqual(r.points[3].stop, false);
});

test("einfuegen: hoechstens 25 Punkte, nichts zu tun -> null", () => {
  const many = Array.from({ length: 30 }, (_x, k) => P2(56 + k * 0.1, 12, `W${k}`));
  const r = P.merge([potsdam, odenskog], many);
  assert.strictEqual(r.points.length, 25);
  assert.strictEqual(P.merge([potsdam, odenskog], [P2(52.3901, 13.0601, "dup")]), null);
  assert.strictEqual(P.merge(Array.from({ length: 25 }, (_x, k) => P2(50 + k, 10, "x")), etappe), null);
});

test("einfuegen: nur ein eigener Punkt -> Etappe davor oder dahinter", () => {
  const r = P.merge([potsdam], etappe);
  assert.deepStrictEqual(r.points.map((p) => p.name), ["Am Pilz", "1.1 Trelleborg", "1.3 Goeteborg", "1.8 Karlstad"]);
});

// Orte an der Strecke (aus der Analyse) – Zwischenpunkte rasten dort ein und tragen den Ortsnamen
test("trackFrom legt Orte der Analyse auf die Strecke (auch je Etappe)", () => {
  const seg = (a, b) => [{ c: line(b - a, 48, 10 + a * 0.01) }];
  const res = { trip: true, stages: [
    { segments: seg(0, 100), places: [{ name: "Adorf", km: 22.4, lat: 48.005, lon: 10.3, r: 2 }] },
    { segments: seg(99, 200), places: [{ name: "Bestadt", km: 7.5, lat: 48, lon: 11.09, r: 1 },
      { name: "Weitweg", km: 10, lat: 48.5, lon: 11.1, r: 0 }] },
  ] };
  const t = P.trackFrom(res);
  assert.deepStrictEqual(t.places.map((q) => [q.name, q.i]), [["Adorf", 30], ["Bestadt", 109]]);
});

test("automatische Zwischenpunkte: an Orten eingerastet, Stadt vor Dorf, mit Namen", () => {
  const track = line(800);   // ~596 km, Ziel-Abstand 50 km (~67 Punkte)
  const places = [{ name: "Dorf", r: 2, i: 64 }, { name: "Stadt", r: 1, i: 72 }, { name: "Ende", r: 1, i: 790 }];
  const r = P.pick(track, { places });
  const first = r.points[1];
  assert.strictEqual(first.name, "Stadt");
  assert.strictEqual(first.lon, track[72][1]);
  assert.strictEqual(r.points[r.points.length - 1].name, "Ende", "Ziel ohne Wegpunkt bekommt den Ort daneben");
  assert.ok(r.points.every((p, k) => !k || p.lon > r.points[k - 1].lon), "Reihenfolge bleibt");
});

test("ohne Ort im Fenster bleibt der Punkt auf der Strecke, Name vom naechsten Ort bis 8 km", () => {
  const track = line(800);
  const r = P.pick(track, { places: [{ name: "Nebenan", r: 2, i: 75 }] });
  assert.strictEqual(r.points[1].name, "Nebenan");
  const r2 = P.pick(track, { places: [] });
  assert.strictEqual(r2.points[1].name, "");
});

// hinten anhängen / vorne anfügen – feste Stelle, Nahtstelle wird Nacht-Punkt
test("merge where=end hängt hinten an, bisheriges Ziel wird Nacht", () => {
  const ex = [{ lat: 48, lon: 11, name: "A" }, { lat: 48, lon: 12, name: "B" }];
  const blk = [{ lat: 47, lon: 14, name: "C" }, { lat: 47, lon: 15, name: "D" }];   // naeher am Start: auto wuerde vorne einfuegen
  const m = P.merge(ex, blk, 25, "end");
  assert.deepStrictEqual(m.points.map((p) => p.name), ["A", "B", "C", "D"]);
  assert.deepStrictEqual(m.points.map((p) => !!p.stop), [false, true, false, false]);
  assert.strictEqual(m.reversed, false);
});

test("merge where=start fügt vorne an, bisheriger Start wird Nacht", () => {
  const ex = [{ lat: 48, lon: 11, name: "A" }, { lat: 48, lon: 12, name: "B" }];
  const blk = [{ lat: 49, lon: 9, name: "X" }, { lat: 49, lon: 10, name: "Y" }];
  const m = P.merge(ex, blk, 25, "start");
  assert.deepStrictEqual(m.points.map((p) => p.name), ["X", "Y", "A", "B"]);
  assert.deepStrictEqual(m.points.map((p) => !!p.stop), [false, false, true, false]);
});

test("merge where=end: doppelter Startpunkt des Imports fällt weg, Naht bleibt Nacht", () => {
  const ex = [{ lat: 48, lon: 11, name: "A" }, { lat: 48, lon: 12, name: "B" }];
  const blk = [{ lat: 48.001, lon: 12.001, name: "B2" }, { lat: 48, lon: 13, name: "C" }];
  const m = P.merge(ex, blk, 25, "end");
  assert.deepStrictEqual(m.points.map((p) => [p.name, !!p.stop]), [["A", false], ["B", true], ["C", false]]);
});
