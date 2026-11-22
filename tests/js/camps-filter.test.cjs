// node --test tests/js/  –  reine Filterlogik fuer „Uebernachten & Versorgen“
const test = require("node:test");
const assert = require("node:assert");
const C = require("../../app/static-rig/camps-filter.js");

const veh = { length: 12.5, weight: 5 };
const camp = (flags, extra = {}) => ({ group: "camp", label: "Wohnmobil-Stellplatz", km: 1, flags: { kind: "caravan_site", ...flags }, ...extra });
const water = (kind) => ({ group: "camp", label: "Frischwasser", km: 1, flags: { water: true }, water_kind: kind });
const at = (iso) => new Date(iso);

test("Gruppen-Filter: Ver-/Entsorgung", () => {
  const pois = [camp({ dump: true, water: true, power: false }), camp({ dump: false })];
  assert.strictEqual(C.filterCamps(pois, { dump: true }, veh, at("2026-07-01T12:00")).length, 1);
  assert.strictEqual(C.filterCamps(pois, { power: true }, veh, at("2026-07-01T12:00")).length, 0);
});

test("Gespann: zu kurz raus, unbekannt bleibt", () => {
  const pois = [camp({ maxlength: 10, maxweight: 7.5 }), camp({ maxlength: null, maxweight: null })];
  const r = C.filterCamps(pois, { fits: true }, veh, at("2026-07-01T12:00"));
  assert.strictEqual(r.length, 1);
  assert.strictEqual(C.fitsRig(r[0].flags, veh), "unknown");
  assert.strictEqual(C.fitsRig({ maxlength: 15, maxweight: 7.5 }, veh), "ok");
});

test("Kosten & Zeiten", () => {
  const pois = [camp({ fee: false }), camp({ fee: true }), camp({ fee: null })];
  assert.strictEqual(C.filterCamps(pois, { free: true }, veh, at("2026-07-01T12:00")).length, 2);
  const yr = [camp({ seasonal: "summer" }), camp({ seasonal: "no" }), camp({ seasonal: null })];
  assert.strictEqual(C.filterCamps(yr, { yearRound: true }, veh, at("2026-07-01T12:00")).length, 2);
  const cap = [camp({ capacity: "5" }), camp({ capacity: "30" }), camp({ capacity: "" })];
  assert.strictEqual(C.filterCamps(cap, { minCap: 10 }, veh, at("2026-07-01T12:00")).length, 2);
});

test("Komfort: unbekannt bleibt drin", () => {
  const pois = [camp({ shower: true }), camp({ shower: false }), camp({ shower: null }), camp({ dog: "no" }), camp({ dog: "leashed" })];
  assert.strictEqual(C.filterCamps(pois, { shower: true }, veh, at("2026-07-01T12:00")).length, 4);
  assert.strictEqual(C.filterCamps(pois, { dog: true }, veh, at("2026-07-01T12:00")).length, 4);
});

test("Wasser: Standard nur oeffentlich (+unbekannt), Unterwahl erweitert", () => {
  const pois = [water("public"), water("guests"), water("tap"), water("unknown")];
  assert.strictEqual(C.filterCamps(pois, {}, veh, at("2026-07-01T12:00")).length, 2);
  assert.strictEqual(C.filterCamps(pois, { waterKinds: ["public", "guests", "tap"] }, veh, at("2026-07-01T12:00")).length, 4);
});

test("nur nahe Etappenziel", () => {
  const pois = [camp({}, { near_stage: 2 }), camp({})];
  assert.strictEqual(C.filterCamps(pois, { nearStage: true }, veh, at("2026-07-01T12:00")).length, 1);
});

test("Oeffnungszeiten (Review Focus 3)", () => {
  assert.strictEqual(C.isOpen("24/7", null, at("2026-07-05T21:00")), true);
  assert.strictEqual(C.isOpen("Mo-Su 08:00-20:00", null, at("2026-07-05T21:00")), false);
  assert.strictEqual(C.isOpen("Mo-Su 08:00-20:00", null, at("2026-07-05T12:00")), true);
  assert.strictEqual(C.isOpen("Apr-Oct", null, at("2026-11-01T12:00")), false);
  assert.strictEqual(C.isOpen("Apr-Oct", null, at("2026-05-01T12:00")), true);
  assert.strictEqual(C.isOpen("Mo-Su 08:00-20:00; Nov-Mar off", null, at("2026-01-15T12:00")), false);
  assert.strictEqual(C.isOpen("Mo-Fr 08:00-12:00,14:00-18:00", null, at("2026-07-06T15:00")), true);
  assert.strictEqual(C.isOpen("Sa,Su 09:00-11:00", null, at("2026-07-06T10:00")), false);
  assert.strictEqual(C.isOpen("unsinn", null, at("2026-07-01T12:00")), null);
  assert.strictEqual(C.isOpen("", null, at("2026-07-01T12:00")), null);
  assert.strictEqual(C.isOpen("", "Apr-Oct", at("2026-12-01T12:00")), false);
  const pois = [camp({ hours: "Apr-Oct" }), camp({ hours: "unsinn" })];
  assert.strictEqual(C.filterCamps(pois, { openNow: true }, veh, at("2026-12-01T12:00")).length, 1);
});

test("Wasser ohne Einstufung (alte Analyse) gilt als unbekannt und bleibt drin", () => {
  const old = { group: "camp", label: "Frischwasser", km: 1, flags: { water: true } };
  assert.strictEqual(C.filterCamps([old, water(null)], {}, veh, at("2026-07-01T12:00")).length, 2);
});

test("insertIndex: zwischen die naechsten Punkte, nie hinter das Ziel", () => {
  const pts = [{ lat: 48, lon: 11 }, { lat: 46, lon: 8 }, { lat: 44, lon: 6 }];
  assert.strictEqual(C.insertIndex(pts, { lat: 47, lon: 9.5 }), 1);
  assert.strictEqual(C.insertIndex(pts, { lat: 43, lon: 5 }), 2);
  assert.strictEqual(C.insertIndex([], { lat: 1, lon: 1 }), 0);
});

test("Entsorgung auf dem Platz / nur Kunden zaehlt wie Gaeste-Wasser", () => {
  const dump = (kind) => ({ group: "camp", label: "Entsorgung", km: 1, flags: { dump: true }, water_kind: kind });
  assert.strictEqual(C.filterCamps([dump("public"), dump("guests"), dump("unknown")], {}, veh, at("2026-07-01T12:00")).length, 2);
  assert.strictEqual(C.filterCamps([dump("guests")], { waterKinds: ["public", "guests"] }, veh, at("2026-07-01T12:00")).length, 1);
});

test("Oeffnungszeiten: Zeitspanne mit drei Teilen ist unlesbar (wie in der App)", () => {
  assert.strictEqual(C.isOpen("08:00-12:00-14:00", null, at("2026-07-01T10:00")), null);
});

test("placeNight: Nacht-Punkt in der Naehe wird ersetzt statt Mini-Etappe", () => {
  const pts = [{ lat: 48, lon: 11 }, { lat: 46, lon: 8, stop: true }, { lat: 44, lon: 6 }];
  assert.deepStrictEqual(C.placeNight(pts, { lat: 46.02, lon: 8.03 }), { index: 1, replace: true });
  assert.deepStrictEqual(C.placeNight(pts, { lat: 47, lon: 9.5 }), { index: 1, replace: false });
  assert.deepStrictEqual(C.placeNight(pts, { lat: 44.01, lon: 6.01 }), { index: 2, replace: false });   // Ziel nie ersetzen
});

test("fitsRig: Hoehe und „keine Wohnmobile“ (0.24.0)", () => {
  const C = require("../../app/static-rig/camps-filter.js");
  const veh = { length: 7.4, weight: 3.5, height: 3.1 };
  assert.strictEqual(C.fitsRig({ maxlength: 10, maxweight: 7.5, maxheight: 3.0 }, veh), "no");
  assert.strictEqual(C.fitsRig({ maxlength: 10, maxweight: 7.5, maxheight: 3.5 }, veh), "ok");
  assert.strictEqual(C.fitsRig({ motorhome: false }, veh), "no");
  assert.strictEqual(C.fitsRig({ maxheight: 3.5 }, veh), "unknown");
});

test("Laeden: Wochentage, mehrere Zeitfenster, off, PH, Muell", () => {
  const oh = "Mo-Fr 07:00-20:00; Sa 07:00-18:00; Su off";
  assert.strictEqual(C.isOpen(oh, null, at("2026-07-05T10:00")), false);   // So
  assert.strictEqual(C.isOpen(oh, null, at("2026-07-04T17:59")), true);    // Sa
  assert.strictEqual(C.isOpen(oh, null, at("2026-07-04T18:01")), false);
  assert.strictEqual(C.isOpen("Mo-Sa 06:30-12:00,14:00-18:00", null, at("2026-07-07T13:00")), false);   // Di
  assert.strictEqual(C.isOpen("Mo-Sa 06:30-12:00,14:00-18:00", null, at("2026-07-07T15:00")), true);
  assert.strictEqual(C.isOpen("PH off; Mo-Sa 08:00-20:00", null, at("2026-07-06T09:00")), true);   // Mo, PH nicht ausgewertet
  assert.strictEqual(C.isOpen("PH off", null, at("2026-07-06T09:00")), null);
  assert.strictEqual(C.isOpen("Mo-Sa 07:00-21:00", null, at("2026-07-06T21:30")), false);
  assert.strictEqual(C.isOpen("nach Vereinbarung", null, at("2026-07-06T09:00")), null);
  assert.strictEqual(C.isOpen("Mo-Fr 07:00-20:00; ???", null, at("2026-07-06T09:00")), null);
});

test("nextChange: bis wann offen / wann oeffnet", () => {
  const oh = "Mo-Fr 07:00-20:00; Sa 07:00-18:00; Su off";
  assert.deepStrictEqual(C.nextChange(oh, at("2026-07-04T10:00")), { open: true, at: "18:00" });
  assert.deepStrictEqual(C.nextChange(oh, at("2026-07-04T05:00")), { open: false, at: "07:00" });
  assert.strictEqual(C.nextChange(oh, at("2026-07-04T19:00")), null);   // heute nicht mehr
  assert.strictEqual(C.nextChange("Mist", at("2026-07-04T10:00")), null);
});

test("PH-Regel neben normalen Regeln: Sonntag zu, nur PH -> unbekannt", () => {
  const oh = "Mo-Sa 08:00-20:00; PH off";
  assert.strictEqual(C.isOpen(oh, null, at("2026-07-05T10:00")), false);
  assert.strictEqual(C.isOpen(oh, null, at("2026-07-06T09:00")), true);
  assert.strictEqual(C.isOpen("PH off", null, at("2026-07-06T09:00")), null);
});

test("Leerzeichen in Tageslisten und Komma zwischen Regeln", () => {
  const a = "Mo-Sa 07:00-20:00; Su, PH off";
  assert.strictEqual(C.isOpen(a, null, at("2026-11-15T11:00")), false);   // So
  assert.strictEqual(C.isOpen(a, null, at("2026-11-16T10:00")), true);    // Mo
  const b = "Mo-Fr 07:00-18:00, Sa 07:00-13:00";
  assert.strictEqual(C.isOpen(b, null, at("2026-11-14T12:00")), true);    // Sa
  assert.strictEqual(C.isOpen(b, null, at("2026-11-14T14:00")), false);
  assert.strictEqual(C.isOpen(b, null, at("2026-11-15T11:00")), false);   // So
  assert.strictEqual(C.isOpen('"nach Vereinbarung"', null, at("2026-11-16T10:00")), null);
});

test("fitsRig: Laenge aus Website/Luftbild (0.53.0)", () => {
  const veh = { length: 12, weight: 7.5, height: 3.2, trailer: true };
  const L = (m, level, kind = "max") => ({ laenge: { m, level, kind } });
  assert.strictEqual(C.fitsRig(L(10, "confirmed"), veh), "no");
  assert.strictEqual(C.fitsRig(L(10, "likely"), veh), "likely_no");
  assert.strictEqual(C.fitsRig(L(13, "likely"), veh), "likely");
  assert.strictEqual(C.fitsRig({ ...L(12, "confirmed", "min"), maxweight: 8 }, veh), "ok");
  assert.strictEqual(C.fitsRig({ ...L(13, "confirmed"), trailer: false }, veh), "no");
  assert.strictEqual(C.fitsRig({ maxlength: 8, ...L(13, "likely") }, veh), "no");   // OSM-Angabe geht vor
});

test("filterCamps: lenKnown laesst nur geprueft passende Plaetze durch (0.53.0)", () => {
  const veh = { length: 12, weight: 7.5, height: 3.2 };
  const pois = [
    { group: "camp", label: "Stellplatz", flags: { laenge: { m: 13, level: "likely", kind: "max" } } },
    { group: "camp", label: "Stellplatz", flags: { laenge: { m: 10, level: "likely", kind: "max" } } },
    { group: "camp", label: "Stellplatz", flags: {} },
    { group: "camp", label: "Entsorgung", flags: { dump: true } },
  ];
  assert.strictEqual(C.filterCamps(pois, { lenKnown: true }, veh, new Date()).length, 2);   // passt + Station
  assert.strictEqual(C.filterCamps(pois, { fits: true }, veh, new Date()).length, 3);       // nur „wohl zu kurz“ raus
});

test("weekHours: Woche ab heute fuer die Detail-Karte (0.54.0)", () => {
  const mo = at("2026-11-23T10:00");   // Montag
  const w = C.weekHours("Mo-Fr 08:30-12:30,14:00-19:00; Sa 09:00-13:00", mo);
  assert.strictEqual(w.length, 7);
  assert.deepStrictEqual(w[0], { day: "heute", text: "08:30–12:30, 14:00–19:00" });
  assert.deepStrictEqual(w[5], { day: "Sa", text: "09:00–13:00" });
  assert.deepStrictEqual(w[6], { day: "So", text: "geschlossen" });
  assert.strictEqual(C.weekHours("24/7", mo)[3].text, "24 Stunden");
  assert.strictEqual(C.weekHours("", mo), null);
  assert.strictEqual(C.weekHours("nach Absprache", mo), null);
});
