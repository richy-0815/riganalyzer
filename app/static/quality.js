/* Datenqualitaet einer Route – Ride und Rig. Im Browser als window.Quality, in Node per require.
   r.quality kommt aus analyze._quality(); Reisen mit Etappen werden nach km gewichtet zusammengefasst. */
(function (root) {
  const GRADE = { gut: "gut", brauchbar: "brauchbar", "dünn": "dünn" };
  const gradeOf = (score) => (score >= 80 ? "gut" : score >= 55 ? "brauchbar" : "dünn");

  // [{km, quality}] -> eine quality (Anteile nach km gewichtet, Zaehler addiert, Datenstand = aeltester)
  function merge(parts) {
    const ok = parts.filter((p) => p.quality);
    if (!ok.length) return null;
    const km = ok.reduce((a, p) => a + (p.km || 0), 0) || 1;
    const avg = (k) => {
      const have = ok.filter((p) => p.quality[k] != null);
      if (!have.length) return undefined;
      const w = have.reduce((a, p) => a + (p.km || 0), 0) || 1;
      return Math.round(have.reduce((a, p) => a + p.quality[k] * (p.km || 0), 0) / w);
    };
    const q = { matched: avg("matched"), surface: avg("surface"), speed: avg("speed"), width: avg("width"),
      elevation: ok.every((p) => p.quality.elevation), reconstructed: ok.some((p) => p.quality.reconstructed),
      source: ok.every((p) => p.quality.source === "lokal") ? "lokal" : "teils live",
      osm_date: ok.map((p) => p.quality.osm_date).filter(Boolean).sort()[0] || null,
      score: Math.round(ok.reduce((a, p) => a + p.quality.score * (p.km || 0), 0) / km) };
    const sub = (key, fields) => {
      const have = ok.filter((p) => p.quality[key]);
      if (!have.length) return undefined;
      const n = have.reduce((a, p) => a + p.quality[key].n, 0);
      const out = { n };
      for (const f of fields) {
        const w = have.filter((p) => p.quality[key][f] != null);
        const nn = w.reduce((a, p) => a + p.quality[key].n, 0);
        out[f] = nn ? Math.round(w.reduce((a, p) => a + p.quality[key][f] * p.quality[key].n, 0) / nn) : null;
      }
      return out;
    };
    q.fuel = sub("fuel", ["diesel", "adblue", "hours"]);
    q.camps = sub("camps", ["details"]);
    q.grade = gradeOf(q.score);
    return q;
  }

  function ageDays(date, now) {
    if (!date) return null;
    return Math.max(0, Math.floor((now - new Date(date + "T00:00")) / 86400000));
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const bar = (label, v, hint) => (v == null ? "" :
    `<div class="q-row"><span>${esc(label)}</span><span class="q-bar"><i style="width:${Math.max(0, Math.min(100, v))}%"></i></span><b>${v} %</b>${hint ? `<small>${esc(hint)}</small>` : ""}</div>`);

  function html(q, now = new Date()) {
    if (!q) return "";
    const age = ageDays(q.osm_date, now);
    const rows = [
      bar("Straßennetz zugeordnet", q.matched, "Rest: Wege, die die Karte nicht kennt – dort ist alles geschätzt"),
      bar("Belag bekannt", q.surface),
      bar("Tempolimit bekannt", q.speed, "sonst gilt das übliche Limit der Straßenart"),
      bar("Breite/Spuren bekannt", q.width, "sonst Schätzung aus der Straßenart"),
    ].join("");
    const facts = [];
    if (q.elevation === false) facts.push("ohne Höhendaten");
    if (q.reconstructed) facts.push("Verlauf aus groben Punkten ergänzt");
    if (q.source && q.source !== "lokal") facts.push(`Kartendaten ${q.source}`);
    if (age != null) facts.push(`Kartenstand ${q.osm_date.split("-").reverse().join(".")} (${age === 0 ? "heute" : `vor ${age} Tag${age === 1 ? "" : "en"}`})`);
    const f = q.fuel, c = q.camps;
    const poi = [];
    if (f && f.n) poi.push(`${f.n} Tankstellen: Diesel-Angabe bei ${f.diesel} %, AdBlue-Angabe bei ${f.adblue} %, Öffnungszeiten bei ${f.hours} %`);
    if (c && c.n) poi.push(`${c.n} Plätze: Kosten/Größe/Zeiten bei ${c.details} % angegeben`);
    return `<div class="q-head"><span class="q-grade q-${q.grade === "dünn" ? "thin" : q.grade}">${GRADE[q.grade]}</span><span>${q.score} von 100</span></div>`
      + rows
      + (poi.length ? `<ul class="q-poi">${poi.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : "")
      + (facts.length ? `<p class="hint">${esc(facts.join(" · "))}</p>` : "")
      + `<p class="hint">Fehlt eine Angabe in OpenStreetMap, rechnet die App mit Annahmen. „Dünn“ heißt: Ergebnis mit Vorsicht lesen.</p>`;
  }

  const api = { merge, html, gradeOf, ageDays };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Quality = api;
})(typeof window !== "undefined" ? window : globalThis);
