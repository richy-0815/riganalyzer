/* Tankplan (RigAnalyzer) – reine Funktionen, im Browser als window.FuelPlan, in Node per require.
   Ohne Live-Preise schaetzt er den Literpreis aus der Art der Tankstelle (Abschlag/Aufschlag auf den
   eigenen Preis im Fahrzeugprofil); kommt spaeter ein echter Preis (s.price), gilt der.
   Abstecher (flags.detour_km, bis 3 km hinter einer Ausfahrt) kosten Sprit und Zeit. */
(function (root) {
  // Aufschlag in €/l gegenueber einer normalen freien Tankstelle (Erfahrungswerte, ohne Gewaehr)
  const DELTA = { rast: 0.30, autohof: 0.04, marke: 0.03, frei: 0, discount: -0.04 };
  const LABEL = { rast: "Raststätte", autohof: "Autohof", marke: "Marke", frei: "frei", discount: "günstig" };
  const EUR_PER_MIN = 0.25;   // Zeitwert fuer den Umweg (15 €/h)
  const DETOUR_KMH = 40;      // Ortsdurchfahrt hin und zurueck
  const RAMP_MIN = 3;         // ab- und wieder auffahren

  const isMw = (s) => !!(s.flags && s.flags.motorway);
  const isDetour = (s) => !!(s.flags && s.flags.detour_km);

  function priceOf(s, base) {
    if (s.price != null) return s.price;
    const b = s.localBase == null ? base : s.localWide ? Math.max(s.localBase, base) : s.localBase;
    return b + (DELTA[(s.flags && s.flags.cls) || "frei"] ?? 0);
  }

  /** Schaetzung an echte Preise in der Naehe angleichen: ohne das wirken Stationen mit echtem Preis
      teurer als geschaetzte (z. B. Frankreich 2,38 echt gegen 2,10 geschaetzt) und der Plan weicht genau auf die
      Stationen aus, deren Preis niemand kennt. Basis = Median der echten Preise bis ±radiusKm (Strecken-km),
      jeweils um den Aufschlag ihrer Art bereinigt; mindestens minN echte Preise, sonst bleibt der Profilpreis. */
  function calibrate(stations, radiusKm = 80, minN = 2, wideKm = 400) {
    const known = stations.filter((p) => p.price != null && p.live)
      .map((p) => ({ km: p.km, base: p.price - (DELTA[(p.flags && p.flags.cls) || "frei"] ?? 0) }));
    if (!known.length) return stations;
    return stations.map((p) => {
      if (p.price != null || p.avgBase) return p;   // Landesschnitt bleibt
      let near = known.filter((k) => Math.abs(k.km - p.km) <= radiusKm).map((k) => k.base);
      // nichts direkt daneben: weiter schauen (gleiche Gegend, oft dasselbe Land), aber nur nach oben angleichen –
      // ein billiges Nachbarland soll die Schaetzung hier nicht kuenstlich druecken
      const wide = near.length < minN;
      if (wide) near = known.filter((k) => Math.abs(k.km - p.km) <= wideKm).map((k) => k.base);
      near.sort((a, b) => a - b);
      if (near.length < minN) return p;
      const m = near.length >> 1;
      const med = near.length % 2 ? near[m] : (near[m - 1] + near[m]) / 2;
      return wide ? { ...p, localBase: med, localWide: true } : { ...p, localBase: med };
    });
  }

  function detourMin(s) {
    return isDetour(s) ? RAMP_MIN + s.flags.detour_km / DETOUR_KMH * 60 : 0;
  }

  // Umweg in €: Sprit fuer die Extra-Kilometer + Zeit
  function detourCost(s, consPerKm, base) {
    if (!isDetour(s)) return 0;
    return s.flags.detour_km * consPerKm * base + detourMin(s) * EUR_PER_MIN;
  }

  /* total: Streckenlaenge, stations: nach km sortiert, cap/reserve/startL in l, consPerKm l/km.
     base: eigener Literpreis (fuer Schaetzung und Umweg), prefer: z. B. Lkw-Saeule, avoid: hart meiden,
     wenn es Alternativen gibt. Pro Stopp: die guenstigste Tankstelle im hinteren Teil der Reichweite
     (effektiver Literpreis = geschaetzter Preis + Umweg je getanktem Liter). */
  // unsure: Aufschlag €/l fuer geschaetzte Preise, sobald es echte gibt – bei fast gleichem Preis
  // lieber die Tankstelle, deren Preis man kennt
  // adjust(s): Zu-/Abschlag €/l je Station, z. B. Tageszeit in Deutschland (12-Uhr-Regel) – nur fuer die Wahl
  function planTank({ total, stations, cap, consPerKm, reserve, startL, prefer, avoid, base = 0, unsure = 0, adjust = null }) {
    const stops = [], gaps = [], levels = [[0, startL]];
    let pos = 0, fuel = startL, guard = 0;
    while (guard++ < 200) {
      const reach = pos + Math.max(0, fuel - reserve) / consPerKm;
      if (reach >= total) { levels.push([total, fuel - (total - pos) * consPerKm]); break; }
      let cand = stations.filter((s) => s.km > pos + 0.3 && s.km <= reach);
      if (avoid && cand.length) {   // teure Autobahn-Tankstellen nur, wenn nichts anderes in Reichweite
        const ok = cand.filter((s) => !avoid(s));
        if (ok.length) cand = ok;
      }
      // nicht zu frueh tanken: hintere Haelfte der Reichweite, sonst was da ist
      const late = cand.filter((s) => s.km >= pos + (reach - pos) * 0.5);
      if (late.length) cand = late;
      if (prefer && cand.length) {
        const pref = cand.filter(prefer);
        if (pref.length) cand = pref;
      }
      let st = null, best = Infinity, onRoute = Infinity;
      for (const s of cand) {
        const fill = cap - (fuel - (s.km - pos) * consPerKm);
        const eff = priceOf(s, base) + (s.price == null ? unsure : 0) + (adjust ? adjust(s) : 0)
          + detourCost(s, consPerKm, base) / Math.max(fill, 1);
        if (!isDetour(s)) onRoute = Math.min(onRoute, eff);
        if (eff < best - 0.005 || (Math.abs(eff - best) <= 0.005 && st && s.km > st.km)) { st = s; best = eff; }
      }
      if (!st) {
        // keine Station in Reichweite (mit Reserve): naechste ueberhaupt, Reserve wird angeknabbert
        const next = stations.find((s) => s.km > pos + 0.3);
        gaps.push({ from: pos, to: next ? next.km : total, reach });
        if (!next || next.km > pos + fuel / consPerKm) {
          levels.push([total, fuel - (total - pos) * consPerKm]);
          break;
        }
        st = next;
      }
      const arrive = fuel - (st.km - pos) * consPerKm;
      const extra = isDetour(st) ? st.flags.detour_km * consPerKm : 0;
      levels.push([st.km, arrive], [st.km, cap]);
      stops.push({ ...st, arrive, liters: cap - arrive + extra, price: priceOf(st, base), estimated: st.price == null,
                   detour_min: Math.round(detourMin(st)), detour_eur: detourCost(st, consPerKm, base),
                   // Abstecher: Ersparnis gegenueber der besten Tankstelle direkt an der Strecke im selben Fenster
                   saving: isDetour(st) && onRoute < Infinity ? (onRoute - best) * (cap - arrive) : null });
      pos = st.km; fuel = cap;
    }
    return { stops, gaps, levels, finish: levels[levels.length - 1][1] };
  }

  /** Echte Preise (Tankerkönig) per OSM-Id einspielen; ohne Treffer bleibt die Schaetzung. */
  function withLivePrices(stations, live) {
    return stations.map((p) => {
      const l = live && p.osm && live[p.osm];
      if (!l) return p;
      // Landesschnitt (DK/SE/NO) ist kein echter Preis – nur die Basis der Schaetzung statt des Profilpreises
      if (l.avg) return { ...p, localBase: l.diesel, avgBase: l };
      return { ...p, price: l.diesel, live: l };
    });
  }

  /** Welche Tankstellen nach echten Preisen fragen: die geplanten Stopps zuerst, dazu Alternativen
      bis ±kmRange um jeden Stopp – nicht die ganze Route (Tankerkönig sperrt bei zu vielen Abrufen). */
  function priceCandidates(stations, stops, kmRange = 10, cap = 60) {
    const out = new Map();
    stops.forEach((s) => { const st = stations.find((x) => x.osm === s.osm); if (st) out.set(st.osm, st); });
    stops.forEach((s) => stations.forEach((x) => {
      if (x.osm && Math.abs(x.km - s.km) <= kmRange && !out.has(x.osm)) out.set(x.osm, x);
    }));
    return [...out.values()].slice(0, cap).map((x) => [x.osm, x.lat, x.lon]);
  }

  /** Deutschland seit 1.4.2026: Preise duerfen nur um 12 Uhr steigen. ADAC (Juni 2026): kurz vor 12 am
      guenstigsten, direkt nach 12 Diesel im Schnitt +18 ct, danach faellt es wieder. Grobe Kurve um den Tagesschnitt. */
  function deHourOffset(h) {
    h = ((h % 24) + 24) % 24;
    return h < 12 ? -0.04 : h < 14 ? 0.14 : h < 17 ? 0.09 : h < 20 ? 0.04 : 0;
  }

  const api = { deHourOffset, DELTA, LABEL, isMw, isDetour, priceOf, detourCost, detourMin, planTank, withLivePrices, priceCandidates, calibrate };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FuelPlan = api;
})(typeof window !== "undefined" ? window : globalThis);
