/* Umweltzonen (RigAnalyzer) – Ableitung aus dem Fahrzeugprofil und Urteil je Zone.
   Reine Funktionen, im Browser als window.LezUI, in Node per require; Kotlin (drive/Lez.kt) spiegelt sie 1:1.
   Urteil: ok | no | check. Regelarten: sticker | critair | etiqueta | euro | unknown. */
(function (root) {
  const ICON = { ok: "🟢", no: "🔴", check: "🟠" };
  const KNOWN_FUEL = ["diesel", "petrol", "gas", "electric"];
  const NEED_EURO = "Euro-Norm im Fahrzeugprofil eintragen";

  // Tabellen: je Kraftstoff Euro-Norm -> Wert (Schluessel = kleinste Norm, die gilt)
  const STICKER_DIESEL = [[4, "green"], [3, "yellow"], [2, "red"]];
  const CRITAIR = {
    petrol: [[5, 1], [4, 2], [2, 3]],
    diesel: [[5, 2], [4, 3], [3, 4], [2, 5]],
  };
  const ETIQUETA = { petrol: [[4, "C"], [3, "B"]], diesel: [[6, "C"], [4, "B"]] };
  const ET_RANK = { "0": 0, ECO: 1, C: 2, B: 3 };
  const STICKER_RANK = { green: 0, yellow: 1, red: 2 };
  const STICKER_NAME = { green: "grüne", yellow: "gelbe", red: "rote" };

  const lookup = (tab, euro) => { for (const [min, val] of tab) if (euro >= min) return val; return null; };
  const euroOf = (veh) => { const e = Number(veh && veh.euro); return e >= 1 ? e : 0; };

  function derive(veh) {
    const euro = euroOf(veh), fuel = (veh && veh.fuel) || "diesel";
    if (!KNOWN_FUEL.includes(fuel)) return { sticker: null, critair: null, etiqueta: null };
    if (fuel === "electric") return { sticker: "green", critair: 0, etiqueta: "0" };
    if (fuel === "gas") return { sticker: "green", critair: 1, etiqueta: "ECO" };
    if (!euro) return { sticker: null, critair: null, etiqueta: null };
    if (fuel === "petrol")
      return { sticker: "green", critair: lookup(CRITAIR.petrol, euro), etiqueta: lookup(ETIQUETA.petrol, euro) };
    return { sticker: lookup(STICKER_DIESEL, euro), critair: lookup(CRITAIR.diesel, euro), etiqueta: lookup(ETIQUETA.diesel, euro) };
  }

  const res = (v, text, need) => ({ v, text, need: need || null });
  const dmy = (iso) => iso.slice(8, 10) + "." + iso.slice(5, 7) + "." + iso.slice(0, 4);
  const localToday = () => { const d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };

  // Urteil nur nach Klasse/Vignette/Registrierung, ohne Zeitfenster
  function classVerdict(rule, veh) {
    const d = derive(veh), euro = euroOf(veh), fuel = veh.fuel || "diesel";
    switch (rule.kind) {
      case "sticker": {
        const need = rule.need || "green";
        if (d.sticker && STICKER_RANK[d.sticker] <= STICKER_RANK[need]) return res("ok", "ihr habt die " + STICKER_NAME[d.sticker] + " Plakette ✓");
        return res("no", STICKER_NAME[need] + " Plakette nötig, ihr habt " + (d.sticker ? "die " + STICKER_NAME[d.sticker] : "keine"));
      }
      case "critair": {
        const max = (Number(veh.zgg) || 0) <= 3.5 ? rule.vp : rule.pl;
        if (max == null) return res("ok", "keine Einschränkung für euer Fahrzeug ✓");
        const c = d.critair;
        if (c == null) return res("no", "Crit'Air " + max + " oder besser nötig, ihr habt keine Crit'Air-Klasse");
        if (c > max) return res("no", "Crit'Air " + max + " oder besser nötig, ihr habt Crit'Air " + c);
        if (rule.vignette && !veh.critair_vignette) return res("no", "Crit'Air-Vignette nötig", "Crit'Air-Vignette");
        return res("ok", "ihr habt Crit'Air " + c + " ✓");
      }
      case "etiqueta": {
        const need = String(rule.need), e = d.etiqueta;
        if (e != null && ET_RANK[e] <= ET_RANK[need]) return res("ok", "ihr habt Etikett " + e + " ✓");
        return res("no", "Etikett " + need + " oder besser nötig, ihr habt " + (e ? "Etikett " + e : "keins"));
      }
      case "euro": {
        const min = fuel === "diesel" ? rule.diesel : rule.petrol;
        const good = fuel === "electric" || fuel === "gas" || min == null || euro >= min;
        if (!good) return res("no", "Euro " + min + " oder besser nötig, ihr habt Euro " + euro);
        if (rule.register && !veh.lez_registered) return res("no", "Registrierung nötig", "Registrierung");
        return res("ok", "Euro-Norm reicht ✓");
      }
      default:
        return res("check", "Regel unbekannt – vor Ort prüfen");
    }
  }

  // todayIso optional (Standard: lokales Systemdatum); Tests geben es immer mit.
  function verdict(zone, veh, whenIso, todayIso) {
    const rule = (zone && zone.rule) || { kind: "unknown" };
    veh = veh || {};
    if (!["sticker", "critair", "etiqueta", "euro"].includes(rule.kind))
      return res("check", "Regel unbekannt – vor Ort prüfen");
    const fuel = veh.fuel || "diesel";
    if (!KNOWN_FUEL.includes(fuel)) return res("check", "Kraftstoff im Fahrzeugprofil prüfen");
    if (!euroOf(veh) && fuel !== "electric" && fuel !== "gas") return res("check", NEED_EURO);
    let base = classVerdict(rule, veh);
    const hours = rule.hours && String(rule.hours) !== "24/7" ? String(rule.hours) : null;
    if (hours && base.v !== "ok") base = res("check", "zeitlich begrenzt: " + hours + " – Zeiten prüfen");
    const today = todayIso ? String(todayIso).slice(0, 10) : localToday();
    const day = whenIso ? String(whenIso).slice(0, 10) : today;
    if (zone.since && day < zone.since) {
      const t = "ab " + dmy(zone.since) + ": " + base.text;
      return res(whenIso ? "ok" : "check", t, base.need);
    }
    if (zone.until && day > zone.until) return res("ok", "galt nur bis " + dmy(zone.until));
    return base;
  }

  // Fussabdruck: Rahmen um alle Ringe (poly = [[[lat, lon], ...], ...]) auf 0.01 Grad gerundet; ohne poly "".
  function footprint(z) {
    let a = 1e9, b = 1e9, c = -1e9, d = -1e9;
    for (const ring of z.poly || []) for (const p of ring) {
      a = Math.min(a, p[0]); b = Math.min(b, p[1]); c = Math.max(c, p[0]); d = Math.max(d, p[1]);
    }
    return a > c ? "" : [a, b, c, d].map((v) => Math.round(v * 100) / 100).join(",");
  }

  // Aus mehreren Eintraegen gleichen Namens UND gleichen Gebiets (gestaffelte Stufen) den zum Zeitpunkt gueltigen;
  // je Name+Gebiet einer (Teilzonen gleichen Namens, z. B. Strasbourgs Gemeinden, bleiben getrennt).
  function pickStage(zones, whenIso, todayIso) {
    const day = whenIso ? String(whenIso).slice(0, 10) : (todayIso ? String(todayIso).slice(0, 10) : localToday()), groups = new Map();
    for (const z of zones || []) {
      const k = (z.name != null ? z.name : z.id) + "|" + footprint(z);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(z);
    }
    const since = (z) => z.since || "";
    return [...groups.values()].map((g) => {
      const hit = g.find((z) => since(z) <= day && (!z.until || z.until >= day));
      if (hit) return hit;
      const past = g.filter((z) => since(z) <= day).sort((a, b) => since(b).localeCompare(since(a)));
      if (past.length) return past[0];
      return g.slice().sort((a, b) => since(a).localeCompare(since(b)))[0];
    });
  }

  const api = { ICON, derive, verdict, pickStage };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.LezUI = api;
})(typeof window !== "undefined" ? window : globalThis);
