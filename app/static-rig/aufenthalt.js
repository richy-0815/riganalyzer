/* Aufenthalte an Zwischenpunkten und Fahrpausen (nur RigAnalyzer) – reine Funktionen fuer Planer
   und Fahrplan. Im Browser window.Aufenthalt, in Node per require (tests/js/aufenthalt.test.cjs). */
(function (root) {
  // Auswahl im Planer (Minuten); 0 = kein Aufenthalt
  const OPTIONS = [0, 15, 30, 45, 60, 90, 120, 180, 240, 360];
  function fmt(min) {
    const m = Math.round(min || 0);
    if (m < 60) return `${m} min`;
    return m % 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")} h` : `${m / 60} h`;
  }

  /** Aufenthalte je Etappe aus den Planer-Punkten und den Abschnitten der Planung (/api/plan „legs“: {km, min, to}).
      -> [[{km (ab Etappenstart), min, name}], …] je Etappe. Etappen enden an „Nacht“-Punkten und am Ziel. */
  function byStage(pts, legs) {
    const out = [[]];
    let km = 0;
    for (const leg of legs || []) {
      km += leg.km || 0;
      const p = pts[leg.to];
      if (!p || leg.to >= pts.length - 1) break;
      if (p.stop) { out.push([]); km = 0; continue; }
      if (p.stay > 0) out[out.length - 1].push({ km: Math.round(km * 10) / 10, min: Math.round(p.stay), name: p.name || "" });
    }
    return out;
  }
  const sumMin = (stays) => (stays || []).reduce((a, s) => a + s.min, 0);

  // Fahrpausen (Stunden): „auto“ = bisherige Schätzung 45–105 min je nach Länge, „2h20“ = 20 min nach jeden vollen 2 h
  // Fahrt, „none“ = ohne. Aufenthalte zählen nicht als Fahrpause.
  const PAUSE = { auto: "Pausen geschätzt (45–105 min)", "2h20": "20 min alle 2 h", none: "ohne Pausen" };
  function pauseHours(km, driveH, mode = "auto") {
    if (mode === "none") return 0;
    if (mode === "2h20") return Math.floor(Math.max(driveH, 0) / 2 - 1e-9) * 20 / 60;
    return Math.min(105, Math.max(45, 45 + (km - 250) / 270 * 60)) / 60;
  }

  /** Wetter-Stützpunkte [[lat, lon, isoUtc, km]] (WeatherUI.stagePoints, linear ohne Aufenthalt) um die Aufenthalte
      davor verschieben. k0 = km des Etappenstarts in derselben Zählung wie die Punkte. */
  function shiftPoints(points, stays, k0 = 0) {
    if (!stays || !stays.length) return points;
    return points.map(([la, lo, iso, km]) => {
      const before = stays.filter((s) => s.km + k0 <= km + 0.05).reduce((a, s) => a + s.min, 0);
      return before ? [la, lo, new Date(Date.parse(iso) + before * 60000).toISOString(), km] : [la, lo, iso, km];
    });
  }

  const api = { OPTIONS, fmt, byStage, sumMin, PAUSE, pauseHours, shiftPoints };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Aufenthalt = api;
})(typeof window !== "undefined" ? window : globalThis);
