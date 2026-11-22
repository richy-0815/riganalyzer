/* Filter fuer „Uebernachten & Versorgen“ (RigAnalyzer) – reine Funktionen, im Browser als
   window.CampFilter und in Node (Tests) per require. Unbekannte Angaben (OSM leer) bleiben drin. */
(function (root) {
  const DAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  /** "ok" | "likely" | "unknown" | "likely_no" | "no". Zusaetzlich zaehlt f.laenge (Urteil vom Server: Website/Text =
   *  level "confirmed", Luftbild/OSM-Stellflaechen = "likely"), wenn OSM keine maxlength hat. */
  function fitsRig(f, veh) {
    const ml = f.maxlength ?? null, mw = f.maxweight ?? null, mh = f.maxheight ?? null;
    // auch Hoehe (Schranke/Tor) und „Wohnmobile nicht erlaubt“
    if ((ml !== null && veh.length > ml) || (mw !== null && veh.weight > mw) || (mh !== null && veh.height > mh)
      || f.motorhome === false) return "no";
    if (ml === null && f.laenge && f.laenge.m && veh.length) {
      const lg = f.laenge, sure = lg.level === "confirmed";
      if (veh.length > lg.m) return sure && lg.kind === "max" ? "no" : "likely_no";
      if (f.trailer === false && veh.trailer) return "no";
      return sure && mw !== null ? "ok" : "likely";
    }
    return ml === null || mw === null ? "unknown" : "ok";
  }

  const range = (list, from, to) => {
    const a = list.indexOf(from), b = list.indexOf(to);
    if (a < 0 || b < 0) return null;
    const out = [];
    for (let i = a; ; i = (i + 1) % list.length) { out.push(i); if (i === b) break; }
    return out;
  };

  // "Mo-Fr,Sa" / "Apr-Oct" -> Indexmenge oder null (unlesbar)
  function parseSet(tok, list) {
    const set = new Set();
    for (const part of tok.split(",")) {
      const [a, b] = part.split("-");
      const r = b ? range(list, a, b) : (list.includes(a) ? [list.indexOf(a)] : null);
      if (!r) return null;
      r.forEach((x) => set.add(x));
    }
    return set;
  }

  const minutes = (hm) => { const m = /^(\d{1,2}):(\d{2})$/.exec(hm); return m ? +m[1] * 60 + +m[2] : null; };

  const inSpan = (min, [a, b]) => (b > a ? min >= a && min < b : min >= a || min < b);
  const hhmm = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

  /** Zeitfenster [von, bis] (Minuten) der Regeln fuer Monat/Wochentag; null = unlesbar, sonst
   *  {matched, windows, ph}. Spaetere Regeln ueberschreiben fruehere. „PH“ (Feiertag) wird erkannt, aber nicht
   *  ausgewertet (kein Feiertagskalender): solche Regeln gelten als nicht zutreffend. */
  const WD = "(?:Mo|Tu|We|Th|Fr|Sa|Su|PH)";
  /** Regeltexte: Leerraum in Tageslisten ("Su, PH off") toleriert, ", " vor neuem Wochentag-Selektor trennt Regeln. */
  function splitRules(oh) {
    return oh
      .replace(new RegExp(`(?<=\\b${WD}),\\s+(?=${WD}\\b)`, "g"), ",")
      .replace(new RegExp(`,\\s+(?=${WD}(?:[-,]${WD})*\\s+(?:\\d|off|closed))`, "g"), ";")
      .split(";").map((s) => s.trim()).filter(Boolean);
  }

  function rulesToday(oh, month, day) {
    let windows = [], matched = false, ph = false, other = false;
    for (const rule of splitRules(oh)) {
      let toks = rule.split(/\s+/), months = null, days = null, times = null, off = false;
      if (toks[0] && /^[A-Z][a-z]{2}(-[A-Z][a-z]{2})?(,|$)/.test(toks[0]) && MONTHS.includes(toks[0].slice(0, 3))) {
        months = parseSet(toks.shift(), MONTHS);
        if (!months) return null;
      }
      if (toks[0] && /^(PH|[A-Z][a-z](-[A-Z][a-z])?)(,(PH|[A-Z][a-z](-[A-Z][a-z])?))*$/.test(toks[0])) {
        const parts = toks.shift().split(",");
        const hasPh = parts.includes("PH"), rest = parts.filter((x) => x !== "PH");
        if (hasPh) ph = true;
        if (!rest.length) {   // reine Feiertagsregel
          if (toks[0] && !/^(off|closed)$/.test(toks[0]) && !toks.every((t) => /^\d/.test(t))) return null;
          continue;
        }
        other = true;
        days = parseSet(rest.join(","), DAYS);
        if (!days) return null;
      }
      if (toks[0] && /^(off|closed)$/.test(toks[0])) { off = true; toks.shift(); }
      else if (toks[0]) {
        times = toks.shift().split(",").map((span) => span.split("-"));
        if (times.some((t) => t.length !== 2)) return null;
        times = times.map((t) => t.map(minutes));
        if (times.some(([a, b]) => a === null || b === null)) return null;
      }
      if (toks.length || (!months && !days && !times && !off)) return null;
      other = true;
      if ((months && !months.has(month)) || (days && !days.has(day))) continue;
      matched = true;
      windows = off ? [] : times || [[0, 1440]];
    }
    return { matched, windows, ph: ph && !other };
  }

  function seasonClosed(seasonal, month) {
    if (!seasonal || seasonal === "no" || seasonal === "yes") return false;
    const ms = parseSet(seasonal.trim(), MONTHS);
    return !!ms && !ms.has(month);
  }

  /** true / false / null (= unbekannt bzw. nicht lesbar) */
  function isOpen(oh, seasonal, now) {
    const month = now.getMonth(), day = now.getDay(), min = now.getHours() * 60 + now.getMinutes();
    if (seasonClosed(seasonal, month)) return false;
    oh = (oh || "").trim();
    if (!oh) return null;
    if (oh === "24/7") return true;
    const r = rulesToday(oh, month, day);
    if (!r) return null;
    if (!r.matched) return r.ph ? null : false;
    return r.windows.some((w) => inSpan(min, w));
  }

  /** Fuer heute: {open:true, at:"HH:MM"} (bis wann) bzw. {open:false, at:"HH:MM"} (wann es oeffnet);
   *  null = unlesbar, 24/7 oder heute nicht mehr offen/zu bekannt. */
  function nextChange(oh, now) {
    oh = (oh || "").trim();
    if (!oh || oh === "24/7") return null;
    const min = now.getHours() * 60 + now.getMinutes();
    const r = rulesToday(oh, now.getMonth(), now.getDay());
    if (!r || !r.matched) return null;
    const cur = r.windows.find((w) => inSpan(min, w));
    if (cur) return cur[1] >= 1440 ? null : { open: true, at: hhmm(cur[1]) };
    const next = r.windows.filter(([a]) => a > min).sort((x, y) => x[0] - y[0])[0];
    return next ? { open: false, at: hhmm(next[0]) } : null;
  }

  /** Woche ab heute fuer die Detail-Karte: [{day: "heute"|"Mo"…, text: "08:30–12:30, 14:00–19:00" |
   *  "geschlossen" | "24 Stunden" | "?"}]; null, wenn keine Zeiten bekannt oder die Angabe nicht lesbar ist. */
  const DAY_DE = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
  function weekHours(oh, now) {
    oh = (oh || "").trim();
    if (!oh) return null;
    const out = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i);
      let text;
      if (oh === "24/7") text = "24 Stunden";
      else {
        const r = rulesToday(oh, d.getMonth(), d.getDay());
        if (!r) return null;
        if (!r.matched) text = r.ph ? "?" : "geschlossen";
        else if (!r.windows.length) text = "geschlossen";
        else if (r.windows.length === 1 && r.windows[0][0] === 0 && r.windows[0][1] >= 1440) text = "24 Stunden";
        else text = r.windows.slice().sort((a, b) => a[0] - b[0]).map(([a, b]) => `${hhmm(a)}–${hhmm(b)}`).join(", ");
      }
      out.push({ day: i === 0 ? "heute" : DAY_DE[d.getDay()], text });
    }
    return out;
  }

  // Ver-/Entsorgungsstellen: Zugang public/guests/tap/unknown (auch Entsorgung)
  const isStation = (p) => p.label === "Frischwasser" || p.label === "Trinkwasser" || p.label === "Entsorgung";

  /** f: {dump, water, power, fits, lenKnown, minCap, free, openNow, yearRound, shower, toilets, wifi, dog,
   *      waterKinds (Standard ["public"]), nearStage} */
  function filterCamps(pois, f, veh, now) {
    const kinds = f.waterKinds || ["public"];
    return pois.filter((p) => {
      if (p.group !== "camp") return false;
      const x = p.flags || {};
      const wk = p.water_kind || "unknown";   // alte Analysen ohne Einstufung
      if (isStation(p) && wk !== "unknown" && !kinds.includes(wk)) return false;
      if (f.dump && !x.dump) return false;
      if (f.water && !x.water) return false;
      if (f.power && !x.power) return false;
      const fit = (f.fits || f.lenKnown) && !isStation(p) ? fitsRig(x, veh) : "";
      if (f.fits && (fit === "no" || fit === "likely_no")) return false;
      if (f.lenKnown && !isStation(p) && fit !== "ok" && fit !== "likely") return false;   // nur, was laut Angabe/Luftbild passt
      if (f.minCap && x.capacity && +x.capacity < f.minCap) return false;
      if (f.free && x.fee === true) return false;
      if (f.openNow && isOpen(x.hours, x.seasonal, now) === false) return false;
      if (f.yearRound && x.seasonal && x.seasonal !== "no") return false;
      for (const k of ["shower", "toilets", "wifi"]) if (f[k] && x[k] === false) return false;
      if (f.dog && x.dog === "no") return false;
      if (f.nearStage && !p.near_stage) return false;
      return true;
    });
  }

  /** Index, an dem ein Platz als Nacht-Punkt in die Punktliste kommt: zwischen die zwei Punkte mit dem
   *  kleinsten Umweg (Luftlinie), nie hinter das Ziel. */
  function insertIndex(points, p) {
    if (points.length < 2) return points.length;
    const d = (a, b) => Math.hypot(a.lat - b.lat, (a.lon - b.lon) * Math.cos((a.lat * Math.PI) / 180));
    let best = 1, cost = Infinity;
    for (let i = 1; i < points.length; i++) {
      const c = d(points[i - 1], p) + d(p, points[i]) - d(points[i - 1], points[i]);
      if (c < cost) { cost = c; best = i; }
    }
    return best;
  }

  /** Wohin ein uebernommener Platz kommt: liegt ein Nacht-Punkt (nicht Start/Ziel) bis km daneben, wird er
   *  ersetzt (sonst gaebe es eine Mini-Etappe), sonst neu eingefuegt wie insertIndex. */
  function placeNight(points, p, km = 10) {
    let best = -1, dist = Infinity;
    for (let i = 1; i < points.length - 1; i++) {
      if (!points[i].stop) continue;
      const d = 111.2 * Math.hypot(points[i].lat - p.lat, (points[i].lon - p.lon) * Math.cos((p.lat * Math.PI) / 180));
      if (d < dist) { dist = d; best = i; }
    }
    return best > 0 && dist <= km ? { index: best, replace: true } : { index: insertIndex(points, p), replace: false };
  }

  const api = { fitsRig, isOpen, nextChange, weekHours, filterCamps, insertIndex, placeNight };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CampFilter = api;
})(typeof window !== "undefined" ? window : globalThis);
