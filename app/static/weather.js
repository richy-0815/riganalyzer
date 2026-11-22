/* Wetter (RigAnalyzer-Fahrplan und RideAnalyzer): Stuetzpunkte, Texte, Warnzeilen –
   reine Funktionen, im Browser window.WeatherUI, in Node per require. Daten kommen von /api/weather. */
(function (root) {
  const ICON = (c) => (c == null ? "" : c <= 1 ? "☀" : c === 2 ? "⛅" : c === 3 ? "☁" : c === 45 || c === 48 ? "🌫"
    : c >= 95 ? "⛈" : (c >= 71 && c <= 77) || c === 85 || c === 86 ? "🌨" : "🌧");
  const num = (v, d = 0) => Number(v).toLocaleString("de-DE", { maximumFractionDigits: d });
  const GUST = { tight: "mit Anhänger vorsichtig", critical: "Fahrt mit Anhänger überdenken", info: "Achtung Seitenwind" };

  /** line: [[lat, lon, km]] einer Etappe -> [[lat, lon, isoUtc, km]] alle kmStep km + Ziel, ETA linear nach km. */
  function stagePoints(line, depMs, arrMs, kmStep = 50) {
    if (!line.length) return [];
    const k0 = line[0][2], k1 = line[line.length - 1][2], span = Math.max(k1 - k0, 0.001);
    const at = (km) => new Date(depMs + ((km - k0) / span) * (arrMs - depMs)).toISOString();
    const out = [[line[0][0], line[0][1], at(k0), Math.round(k0)]];
    let next = k0 + kmStep;
    for (let i = 1; i < line.length; i++) {
      const [la, lo, km] = line[i - 1], [lb, lob, kb] = line[i];
      while (next < k1 && next <= kb) {
        const t = kb > km ? (next - km) / (kb - km) : 0;
        out.push([la + (lb - la) * t, lo + (lob - lo) * t, at(next), Math.round(next)]);
        next += kmStep;
      }
    }
    const last = line[line.length - 1];
    out.push([last[0], last[1], at(k1), Math.round(k1)]);
    return out;
  }

  function stripText(st) {
    if (st.later) return { main: `🗓 Vorhersage ${st.later}`, sub: "", level: null };
    const e = st.end;
    if (!e) return { main: "Wetter gerade nicht abrufbar", sub: "", level: null };
    const main = `${ICON(e.code)} ${num(e.temp)}° · 🌧 ${num(e.precip_mm, 1)} mm · 💨 Böen ${num(e.gust)} km/h`;
    const w = st.worst || {}, sub = [];
    if ((w.precip_mm || 0) >= 1) sub.push(`unterwegs: Regen bis ${num(w.precip_mm, 1)} mm${w.precip_at != null ? ` bei km ${num(w.precip_at)}` : ""}`);
    if (w.gust_level) sub.push(`Böen bis ${num(w.gust)} km/h – ${GUST[w.gust_level]}`);
    return { main, sub: sub.join(" · "), level: w.gust_level || null };
  }

  const ORDER = { red: 0, orange: 1, yellow: 2 };
  const hhmm = (iso) => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });

  function warnRows(ws) {
    return (ws || []).slice().sort((a, b) => (ORDER[a.level] ?? 9) - (ORDER[b.level] ?? 9))
      .map((w) => ({ cls: `w-${w.level}`, text: `⚠ ${w.event} · ${hhmm(w.from)}–${hhmm(w.to)}${w.area ? ` · ${w.area}` : ""}`, source: w.source }));
  }

  // ---- RideAnalyzer: eine Tagestour, Wetter an ~9 Punkten zur Durchfahrt
  const pad = (n) => String(n).padStart(2, "0");
  /** Vorschlag fuer die Abfahrt: bis 11 Uhr heute zur naechsten vollen Stunde, sonst morgen 9 Uhr (Ortszeit). */
  function defaultDeparture(now = new Date()) {
    const d = new Date(now);
    if (now.getHours() < 11) { d.setHours(now.getHours() + 1, 0, 0, 0); return d; }
    d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0);
    return d;
  }
  const localInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

  /** line [[lat, lon, km]] -> n gleichmaessig verteilte Punkte, jeder als eigene „Etappe“ (so liefert die API Wetter je Punkt). */
  function tourPoints(line, depMs, hours, n = 9) {
    if (!line.length) return [];
    const k0 = line[0][2], k1 = line[line.length - 1][2], span = Math.max(k1 - k0, 0.001);
    let j = 1;
    return Array.from({ length: n }, (_, i) => {
      const km = k0 + (span * i) / (n - 1);
      while (j < line.length - 1 && line[j][2] < km) j++;
      const [la, lo, ka] = line[j - 1], [lb, lob, kb] = line[Math.min(j, line.length - 1)];
      const t = kb > ka ? Math.min(Math.max((km - ka) / (kb - ka), 0), 1) : 0;
      const iso = new Date(depMs + Math.round(((km - k0) / span) * hours * 36e5)).toISOString();
      return { n: i + 1, pts: [[la + (lb - la) * t, lo + (lob - lo) * t, iso, Math.round(km)]] };
    });
  }

  /** Eine Zeile je Punkt: km, Uhrzeit, Kurztext, Boeen-Hinweis. */
  function pointRow(st, at) {
    const time = at.iso ? hhmm(at.iso) : "";
    if (st.later) return { km: at.km, time, text: `🗓 Vorhersage ${st.later}`, level: null };
    const e = st.end;
    if (!e) return { km: at.km, time, text: "–", level: null };
    return { km: at.km, time, text: `${ICON(e.code)} ${num(e.temp)}° · ${num(e.precip_mm, 1)} mm · 💨 ${num(e.gust)} km/h`,
      level: (st.worst && st.worst.gust_level) || null };
  }

  /** Warnungen aller Punkte, gleiche Meldung nur einmal. */
  function uniqueWarnings(stages) {
    const seen = new Set();
    return (stages || []).flatMap((s) => s.warnings || []).filter((w) => {
      const k = `${w.level}|${w.event}|${w.from}|${w.to}|${w.area}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }

  const api = { stagePoints, stripText, warnRows, defaultDeparture, localInput, tourPoints, pointRow, uniqueWarnings };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.WeatherUI = api;
})(typeof window !== "undefined" ? window : globalThis);
