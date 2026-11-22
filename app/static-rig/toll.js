/* Maut-Rechnung je Land (RigAnalyzer) – reine Funktionen, im Browser window.Toll, in Node per require.
   Grundlage: mautpflichtige km je Land aus der Analyse (OSM toll=yes auf der gefahrenen Strecke) und das Fahrzeug.
   - Streckenmaut (FR, IT, ES, PT): Maut-km × Satz der Fahrzeugklasse = Schätzung („ca.“), Betreiber weichen ab.
   - Vignette/Pauschale (CH, AT, SI …): fester Betrag je Zeitraum = genau, unabhängig von den km.
   - Sondermaut: Tunnel/Brücken aus sondermaut.json, erkannt vom Server (rig.special), Preis je Klasse und Saison.
   Stand und Quellen stehen in TOLL.meta – einmal im Jahr (Januar) nachziehen. */
(function (root) {
  const TOLL = {
    meta: {
      stand: "2026",
      quellen: {
        FR: "Durchschnitt aus 2026er Klasse-1-Preisen großer Strecken (Paris–Lyon/Marseille/Bordeaux, ≈ 0,09–0,11 €/km), Klassenfaktoren der Betreiber (Kl. 2 ≈ 1,5×, Kl. 3 ≈ 2,4×, Kl. 4 ≈ 3,2×)",
        IT: "Autostrade 2026: Klasse A ≈ 0,07–0,10 €/km, B ≈ 0,10–0,15, 3 ≈ 0,14–0,20, 4 ≈ 0,17–0,25 (Mitte der Spanne)",
        CH: "BAZG/ADAC 2026: Vignette 40 CHF (je Fahrzeug, auch Anhänger), PSVA 3,25 CHF/Tag, mind. 25, höchstens 58,50 für bis 30 Tage",
        AT: "ASFINAG/ADAC 2026: Vignette 1 Tag 9,60 €, 10 Tage 12,80 €, 2 Monate 32 €, Jahr 106,80 €; Anhänger ohne Vignette",
      },
    },
    // €/km je Klasse (Schätzwerte)
    km: {
      FR: { 1: 0.095, 2: 0.147, 3: 0.228, 4: 0.304 },
      IT: { A: 0.085, B: 0.125, 3: 0.17, 4: 0.21 },
      ES: { 1: 0.10, 2: 0.17 },
      PT: { 1: 0.09, 2: 0.16 },
    },
    // Sondermaut-Strecken (Pkw/Wohnmobil bis 3,5 t, einfache Fahrt) – nur als Hinweis, Erkennung folgt
    sonder: {
      AT: "Sondermaut extra: Brenner A13 12,50 €, Tauern A10 15 €, Arlberg 13 €, Karawanken 9 €, Pyhrn (beide Tunnel) 19 €.",
      FR: "Mont-Blanc-/Fréjus-Tunnel und Viaduc de Millau kosten extra.",
    },
  };

  const num = (v, d) => (Number.isFinite(+v) && +v > 0 ? +v : d);
  const de = (v, dig = 2) => v.toLocaleString("de-DE", { minimumFractionDigits: dig, maximumFractionDigits: dig });
  const euro = (v) => `${Math.round(v).toLocaleString("de-DE")} €`;
  const chf = (v) => `${de(v, v % 1 ? 2 : 0)} CHF`;

  /** Mautklasse Frankreich: 1 (≤ 2 m, ≤ 3,5 t), 2 (2–3 m, ≤ 3,5 t; Klasse 1 mit Anhänger), 3 (> 3 m oder > 3,5 t, 2 Achsen),
   *  4 (> 3,5 t mit Anhänger = 3+ Achsen). */
  function classFR(veh) {
    const h = num(veh.height, 2.9), w = num(veh.zgg, num(veh.weight, 3.5));
    if (w > 3.5) return veh.trailer ? 4 : 3;
    if (h > 3) return veh.trailer ? 4 : 3;
    if (h <= 2) return veh.trailer ? 2 : 1;
    return 2;
  }

  /** Mautklasse Italien: A (Höhe an der Vorderachse ≤ 1,30 m), B (darüber, Wohnmobile), 3/4 nach Achsen mit Anhänger. */
  function classIT(veh) {
    const h = num(veh.height, 2.9), w = num(veh.zgg, num(veh.weight, 3.5));
    if (veh.trailer) return w > 3.5 ? 4 : 3;
    if (w > 3.5) return 3;
    return h > 1.6 ? "B" : "A";
  }

  /** Mautklasse Spanien/Portugal: Ligeros = Pkw/Wohnmobil bis 3,5 t, auch mit Anhänger ohne Zwillingsreifen;
   *  darüber Pesados 1. Vorher zählte jedes Wohnmobil über 2 m als Pesados – zu teuer. */
  function classES(veh) {
    return num(veh.zgg, num(veh.weight, 3.5)) > 3.5 ? 2 : 1;
  }

  /** Klasse für eine Sondermaut-Strecke je Tarifschema (sondermaut.json); null = nicht berechenbar. */
  function specialClass(schema, veh) {
    const h = num(veh.height, 2.9), w = num(veh.zgg, num(veh.weight, 3.5));
    if (schema === "FR") return String(classFR(veh));
    if (schema === "hoehe") return h <= 2 ? "1" : h <= 3 ? "2" : veh.trailer ? "4" : "3";   // Mont-Blanc/Fréjus: Gesamthöhe, auch Gespann
    if (schema === "womo") return h > 2 ? "womo" : "pkw";                                   // Gr. St. Bernhard
    if (schema === "at") return w > 3.5 ? null : "bis35";                                   // über 3,5 t steckt es in der GO-Maut
    return null;
  }

  const inSummer = (date, s) => {
    const md = String(date).slice(5, 10);
    return s.von <= s.bis ? md >= s.von && md <= s.bis : md >= s.von || md <= s.bis;
  };

  /** Sondermaut je Durchfahrt. hits = [{id, km, date?}] (date „JJJJ-MM-TT“ der Etappe), rules = sondermaut.json.
   *  -> [{id, cc, name, km, eur, chf, exact, text, detail, quelle}] */
  function special(hits, rules, veh) {
    if (!rules || !Array.isArray(rules.objekte)) return [];
    const byId = Object.fromEntries(rules.objekte.map((o) => [o.id, o]));
    return (hits || []).filter((h) => byId[h.id]).map((h) => {
      const o = byId[h.id], k = specialClass(o.schema, veh);
      const out = { id: o.id, cc: o.cc, name: o.name, km: h.km, eur: 0, chf: 0, exact: !!o.exakt, text: "", detail: "", quelle: o.quelle, maut_km: num(o.maut_km, 0) };
      if (k == null) {
        out.exact = false;
        out.text = o.schema === "at" ? "in der GO-Maut enthalten" : "Preis beim Betreiber prüfen";
        out.detail = o.schema === "at" ? "Über 3,5 t zahlt ihr die Strecke über die GO-Box mit." : "";
        return out;
      }
      let preise = o.preise, note = "";
      if (o.sommer) {
        const summer = h.date ? inSummer(h.date, o.sommer) : true;
        if (summer) preise = { ...o.preise, ...o.sommer.preise };
        const [von, bis] = [o.sommer.von, o.sommer.bis].map((x) => `${x.slice(3)}.${x.slice(0, 2)}.`);
        const other = summer ? o.preise[k] : o.sommer.preise[k];
        if (other != null) note = h.date ? (summer ? ` · Sommerpreis ${von}–${bis}` : ` · Nebensaison (${von}–${bis}: ${de(o.sommer.preise[k])} €)`)
          : ` · ohne Reisedatum Sommerpreis ${von}–${bis}; außerhalb ${von}–${bis}: ${de(o.preise[k])} €`;
      }
      const p = preise[k];
      if (p == null) { out.exact = false; out.text = "Preis beim Betreiber prüfen"; return out; }
      if (o.waehrung === "CHF") out.chf = p; else out.eur = p;
      const kn = { womo: "Wohnmobil", pkw: "Pkw (auch mit Anhänger)", bis35: "bis 3,5 t" }[k] || `Klasse ${k}`;
      out.text = `${out.exact ? "" : "ca. "}${o.waehrung === "CHF" ? chf(p) : `${de(p)} €`}`;
      out.detail = `${kn}, einfache Fahrt${note}${o.zusatz ? ` · ${o.zusatz}` : ""}`;
      return out;
    });
  }

  /** Vignettenzeitraum Österreich nach Reisetagen im Land (bis 3,5 t). */
  function vignetteAT(days) {
    if (days <= 1) return { price: 9.6, label: "1-Tages-Vignette" };
    if (days <= 10) return { price: 12.8, label: "10-Tages-Vignette" };
    if (days <= 61) return { price: 32, label: "2-Monats-Vignette" };
    return { price: 106.8, label: "Jahresvignette" };
  }

  /** Ein Land -> {cc, eur, chf, exact, text, detail}. c = {cc, km, motorway_km, toll_km}; days = Reisetage im Land. */
  function country(c, veh, days = 1) {
    const cc = c.cc, tk = Math.round(num(c.toll_km, 0)), mk = Math.round(num(c.motorway_km, 0));
    const w = num(veh.zgg, num(veh.weight, 3.5)), tr = !!veh.trailer;
    const out = { cc, eur: 0, chf: 0, exact: false, text: "", detail: "" };
    if (cc === "FR" || cc === "IT" || cc === "ES" || cc === "PT") {
      const k = cc === "FR" ? classFR(veh) : cc === "IT" ? classIT(veh) : classES(veh);
      const rate = TOLL.km[cc][k];
      if (!tk) { out.text = mk ? "Keine mautpflichtigen Abschnitte erkannt." : ""; return out; }
      out.eur = tk * rate;
      out.text = `ca. ${euro(out.eur)} Maut`;
      const kname = cc === "ES" || cc === "PT" ? (k === 1 ? "Ligeros" : "Pesados 1") : `Klasse ${k}`;
      out.detail = `${kname} · ${tk} km mautpflichtig × ≈ ${de(rate, 3)} €/km${cc === "FR" ? " (je nach Betreiber ±15 %)" : ""}`;
      if (cc === "IT" && tr) out.detail += " · mit Tandem-Anhänger (2 Achsen) Klasse 4";
      if ((cc === "ES" || cc === "PT") && tr && k === 1) out.detail += " · mit Zwillingsreifen am Anhänger Pesados 1";
      if (TOLL.sonder[cc]) out.detail += ` · ${TOLL.sonder[cc]}`;
      return out;
    }
    if (cc === "CH") {
      if (!mk) return out;
      out.exact = true;
      if (w > 3.5) {
        const psva = Math.min(58.5, Math.max(25, 3.25 * days));
        out.chf = psva + (tr ? 40 : 0);
        out.text = chf(out.chf);
        out.detail = `PSVA ${days} Tag${days > 1 ? "e" : ""} × 3,25 CHF (mind. 25 CHF)${tr ? " + Vignette für den Anhänger 40 CHF" : ""} – gilt für jeden Tag im Land, auch geparkt.`;
      } else {
        out.chf = 40 * (tr ? 2 : 1);
        out.text = `${out.chf} CHF Vignette${tr ? "n" : ""}`;
        out.detail = tr ? "Je eine Vignette für Zugfahrzeug und Anhänger (je 40 CHF, gültig bis 31. Januar des Folgejahres)."
          : "Jahresvignette 40 CHF – eine andere gibt es nicht (gültig bis 31. Januar des Folgejahres).";
      }
      return out;
    }
    if (cc === "AT") {
      if (!mk) return out;
      if (w > 3.5) {
        out.text = "GO-Maut je km (nicht berechnet)";
        out.detail = "Über 3,5 t: fahrleistungsabhängige Maut mit GO-Box, Tarif je EURO- und CO₂-Klasse – vor der Reise im ASFINAG-Tarifrechner prüfen.";
        return out;
      }
      const v = vignetteAT(days);
      out.exact = true; out.eur = v.price;
      out.text = `${de(v.price)} € ${v.label}`;
      out.detail = `Für ${days} Reisetag${days > 1 ? "e" : ""} in Österreich; Anhänger brauchen keine eigene. ${TOLL.sonder.AT}`;
      return out;
    }
    return out;   // anderes Land: bisheriger Kurzhinweis bleibt
  }

  /** Alle Länder + Summe. daysOf(cc) -> Reisetage im Land (Standard 1). */
  function trip(countries, veh, daysOf = () => 1, specials = []) {
    // Maut-km der Sonderstrecken (z. B. A75 nur am Viadukt) nicht noch einmal als Streckenmaut zählen
    const minus = {};
    for (const s of specials) minus[s.cc] = (minus[s.cc] || 0) + (s.maut_km || 0);
    const rows = (countries || []).map((c) => country(minus[c.cc] && c.toll_km ? { ...c, toll_km: Math.max(0, c.toll_km - minus[c.cc]) } : c,
      veh, Math.max(1, daysOf(c.cc) || 1)));
    const all = rows.concat(specials);
    const eur = all.reduce((s, r) => s + r.eur, 0), sumChf = all.reduce((s, r) => s + r.chf, 0);
    const approx = all.some((r) => (r.eur || r.chf) && !r.exact);
    const parts = [];
    if (eur) parts.push(`${approx ? "ca. " : ""}${euro(eur)}`);
    if (sumChf) parts.push(chf(sumChf));
    return { rows, special: specials, eur, chf: sumChf, approx, total: parts.join(" + ") };
  }

  const api = { TOLL, classFR, classIT, classES, specialClass, special, vignetteAT, country, trip };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Toll = api;
})(typeof window !== "undefined" ? window : globalThis);
