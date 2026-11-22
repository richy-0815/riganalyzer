/* Eckdaten aus einem GPX-Import (RigAnalyzer) – reine Funktionen, im Browser als window.ImportPlan,
   in Node per require. Aus der importierten Strecke werden Start, Ziel, Etappen-Enden („Nacht“) und Zwischenpunkte
   fuer den Routenplaner. Zwischenpunkte: Wegpunkte aus der GPX (wpt/rtept), sonst gleichmaessig verteilte Punkte
   auf der Strecke (50–100 km Abstand), damit die neue Route grob dem Original folgt, ohne jede Kurve zu erzwingen.
   Jeder Zwischenpunkt bekommt die Fahrtrichtung mit (heading) – sonst landet er auf der Autobahn-Gegenfahrbahn. */
(function (root) {
  const MAX = 25;              // wie planner.MAX_POINTS
  const NEAR_KM = 1.5;         // Wegpunkt hoechstens so weit neben der Strecke
  const GAP_KM = 2;            // Mindestabstand zwischen zwei Punkten (entlang der Strecke)
  const NAME_KM = 0.3;         // Wegpunkt-Name fuer Start/Ziel/Nacht, wenn so nah dran
  const PLACE_KM = 2.5;        // Ort (aus der Analyse) hoechstens so weit neben der Strecke
  const TOWN_KM = 8;           // Start/Ziel/Nacht ohne Wegpunkt-Namen: Ortsname bis so weit entlang der Strecke

  const rad = (d) => d * Math.PI / 180;
  function dist(a, b) {        // km, Haversine
    const dLa = rad(b[0] - a[0]), dLo = rad(b[1] - a[1]);
    const h = Math.sin(dLa / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLo / 2) ** 2;
    return 12742 * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function bearing(a, b) {     // Grad 0–360, Norden = 0
    const y = Math.sin(rad(b[1] - a[1])) * Math.cos(rad(b[0]));
    const x = Math.cos(rad(a[0])) * Math.sin(rad(b[0])) - Math.sin(rad(a[0])) * Math.cos(rad(b[0])) * Math.cos(rad(b[1] - a[1]));
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
  }
  function cumKm(track) {
    const c = [0];
    for (let i = 1; i < track.length; i++) c.push(c[i - 1] + dist(track[i - 1], track[i]));
    return c;
  }
  function idxAt(cum, km) {    // erster Index mit cum >= km
    let lo = 0, hi = cum.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (cum[m] < km) lo = m + 1; else hi = m; }
    return lo;
  }
  // Fahrtrichtung an Index i: von ca. 150 m davor bis 150 m danach
  function headingAt(track, cum, i) {
    const a = idxAt(cum, Math.max(0, cum[i] - 0.15)), b = Math.min(track.length - 1, idxAt(cum, cum[i] + 0.15));
    if (a === b) return null;
    return Math.round(bearing(track[a], track[b])) % 360;
  }

  /** Strecke aus einem Analyse-Ergebnis: einzelne Route (segments) oder Reise (stages[].segments).
      -> {track: [[lat, lon], …], stageEnds: [Index des letzten Punkts jeder Etappe ausser der letzten],
          places: [{name, r, i}] – benannte Orte an der Strecke (aeltere Analysen: leer), i = Index in track} */
  function trackFrom(result) {
    const track = [], stageEnds = [], raw = [];
    const push = (segs) => (segs || []).forEach((s) => (s.c || []).forEach((p) => {
      const q = [+p[0], +p[1]], l = track[track.length - 1];
      if (!l || l[0] !== q[0] || l[1] !== q[1]) track.push(q);
    }));
    const take = (r, from) => (Array.isArray(r && r.places) ? r.places : []).forEach((q) => {
      if (q && q.name && Number.isFinite(+q.lat) && Number.isFinite(+q.lon)) raw.push({ q, from, to: track.length - 1 });
    });
    if (result && result.trip && Array.isArray(result.stages)) {
      result.stages.forEach((st, k) => {
        const from = Math.max(0, track.length - 1);
        push(st.segments); take(st, from);
        if (k < result.stages.length - 1 && track.length) stageEnds.push(track.length - 1);
      });
    } else { push(result && result.segments); take(result, 0); }
    // Orte auf die Strecke legen: naechster Punkt im Abschnitt der eigenen Etappe, gesucht rund um die km-Angabe
    const cum = cumKm(track), places = [];
    for (const { q, from, to } of raw) {
      const est = Number.isFinite(+q.km) ? idxAt(cum, cum[from] + +q.km) : from;
      const lo = Math.max(from, idxAt(cum, cum[est] - 5)), hi = Math.min(to, idxAt(cum, cum[est] + 5));
      let bi = -1, bd = Infinity;
      for (let i = lo; i <= hi; i++) { const d = dist(track[i], [+q.lat, +q.lon]); if (d < bd) { bd = d; bi = i; } }
      if (bi >= 0 && bd <= PLACE_KM) places.push({ name: String(q.name).trim(), r: Number.isFinite(+q.r) ? +q.r : 2, i: bi });
    }
    places.sort((a, b) => a.i - b.i);
    return { track, stageEnds: [...new Set(stageEnds)].filter((i) => i > 0 && i < track.length - 1), places };
  }

  function nearest(track, p) {
    let best = -1, bd = Infinity;
    for (let i = 0; i < track.length; i++) {
      const d = dist(track[i], p);
      if (d < bd) { bd = d; best = i; }
    }
    return [best, bd];
  }

  /** Planer-Punkte aus der Strecke.
      opts.stageEnds: Indizes der Etappen-Enden (werden „Nacht“), opts.wpts: [{lat, lon, name}] aus der GPX,
      opts.max: hoechstens so viele Punkte (Start + Ziel eingeschlossen).
      -> {points: [{lat, lon, name, stop, heading?}], source: "wpt"|"auto"|"none", km, cut} */
  function pick(track, opts = {}) {
    const max = Math.max(2, Math.min(MAX, opts.max || MAX));
    if (!Array.isArray(track) || track.length < 2) return { points: [], source: "none", km: 0, cut: false };
    const cum = cumKm(track), total = cum[cum.length - 1], last = track.length - 1;
    const wpts = (opts.wpts || []).filter((w) => w && Number.isFinite(+w.lat) && Number.isFinite(+w.lon));
    let ends = [...new Set(opts.stageEnds || [])].filter((i) => i > 0 && i < last).sort((a, b) => a - b);
    let cut = false;
    if (ends.length > max - 2) { ends = ends.slice(0, max - 2); cut = true; }
    const anchors = [0, ...ends, last];
    const places = (opts.places || []).filter((q) => q && q.name && q.i >= 0 && q.i <= last);
    // Name eines Punkts: Wegpunkt aus der GPX (bis 300 m), sonst naechster Ort an der Strecke (Stadt vor Dorf)
    const townNear = (i) => {
      let nm = "", bs = Infinity;
      for (const q of places) {
        const d = Math.abs(cum[q.i] - cum[i]);
        if (d > TOWN_KM) continue;
        const sc = d + q.r * 1.5;
        if (sc < bs) { bs = sc; nm = q.name; }
      }
      return nm;
    };
    const nameNear = (i) => {
      let nm = "", bd = NAME_KM;
      for (const w of wpts) { const d = dist(track[i], [w.lat, w.lon]); if (d <= bd && w.name) { bd = d; nm = String(w.name).trim(); } }
      return nm || townNear(i);
    };
    const farFromAnchors = (i) => anchors.every((a) => Math.abs(cum[a] - cum[i]) >= GAP_KM);
    const budget = max - anchors.length;

    // 1. Wegpunkte aus der GPX, die auf der Strecke liegen, in Fahrtreihenfolge
    let vias = [];
    for (const w of wpts) {
      const [i, d] = nearest(track, [w.lat, w.lon]);
      if (d <= NEAR_KM && farFromAnchors(i)) vias.push({ i, name: w.name ? String(w.name).trim() : "" });
    }
    vias.sort((a, b) => a.i - b.i);
    vias = vias.filter((v, k) => !k || cum[v.i] - cum[vias[k - 1].i] >= GAP_KM);
    const source = vias.length ? "wpt" : "auto";
    if (!vias.length && budget > 0 && total > 0) {
      // 2. sonst gleichmaessig verteilt: Abstand je nach Tourlaenge 50–100 km, notfalls groesser (Punkte-Grenze)
      let spacing = Math.min(100, Math.max(50, total / 20));
      const count = (sp) => anchors.slice(1).reduce((n, b, k) => n + Math.max(0, Math.ceil((cum[b] - cum[anchors[k]]) / sp) - 1), 0);
      while (count(spacing) > budget) spacing *= 1.15;
      anchors.slice(1).forEach((b, k) => {
        const a = anchors[k], len = cum[b] - cum[a], n = Math.max(0, Math.ceil(len / spacing) - 1);
        for (let j = 1; j <= n; j++) {
          // an einen echten Ort an der Strecke einrasten (Stadt vor Dorf, im Fenster ±30 % des Abstands)
          const km = cum[a] + len * j / (n + 1), win = Math.min(spacing * 0.3, len / (n + 1) * 0.45);
          let best = null, bs = Infinity;
          for (const q of places) {
            const d = Math.abs(cum[q.i] - km);
            if (d > win || q.i <= a || q.i >= b) continue;
            const sc = q.r * win + d;
            if (sc < bs) { bs = sc; best = q; }
          }
          vias.push(best ? { i: best.i, name: best.name } : { i: idxAt(cum, km), name: "" });
        }
      });
      vias = vias.filter((v) => farFromAnchors(v.i)).sort((x, y) => x.i - y.i)
        .filter((v, k, arr) => !k || cum[v.i] - cum[arr[k - 1].i] >= GAP_KM);
    }
    if (vias.length > budget) {   // zu viele Wegpunkte: gleichmaessig ausduennen
      const keep = Math.max(0, budget);
      vias = keep ? Array.from({ length: keep }, (_x, k) => vias[Math.round((k + 0.5) * vias.length / keep - 0.5)]) : [];
      cut = true;
    }

    const pt = (i, extra) => ({ lat: +track[i][0].toFixed(6), lon: +track[i][1].toFixed(6), name: nameNear(i), stop: false, ...extra });
    const all = [
      ...anchors.map((i, k) => ({ i, p: pt(i, { stop: k > 0 && k < anchors.length - 1 }) })),
      ...vias.map((v) => {
        const h = headingAt(track, cum, v.i);
        return { i: v.i, p: pt(v.i, { ...(v.name ? { name: v.name } : {}), ...(h == null ? {} : { heading: h }) }) };
      }),
    ].sort((a, b) => a.i - b.i);
    return { points: all.map((x) => x.p), source: vias.length ? source : "none", km: total, cut };
  }

  /** Import in eine bestehende Planung einfuegen: eigene Punkte bleiben, die Import-Punkte kommen als Block
      an die Stelle mit dem kleinsten Umweg (zwischen zwei eigenen Punkten, davor oder dahinter), wenn kuerzer auch
      rueckwaerts. Import-Punkte bis DUP_KM neben einem eigenen Punkt fallen weg; hoechstens max Punkte insgesamt.
      Eingefuegte Punkte sind Zwischenpunkte – Start/Ziel des Imports werden nie zu „Nacht“.
      -> {points, at, reversed, added} oder null, wenn nichts einzufuegen ist
      where: "end" = hinten anhaengen, "start" = vorne anfuegen – feste Stelle, Richtung des Imports,
      und die Nahtstelle (bisheriges Ziel bzw. bisheriger Start) wird zum „Nacht“-Punkt (Etappengrenze). */
  const DUP_KM = 2;
  function merge(existing, block, max = MAX, where = "auto") {
    const ex = (existing || []).filter((p) => p && Number.isFinite(+p.lat) && Number.isFinite(+p.lon));
    const ll = (p) => [+p.lat, +p.lon];
    let blk = (block || []).filter((b) => !ex.some((p) => dist(ll(p), ll(b)) < DUP_KM));
    const room = max - ex.length;
    if (!blk.length || room <= 0) return null;
    if (blk.length > room) blk = Array.from({ length: room }, (_x, k) => blk[Math.round(k * (blk.length - 1) / Math.max(1, room - 1))]);
    let len = 0;
    for (let i = 1; i < blk.length; i++) len += dist(ll(blk[i - 1]), ll(blk[i]));
    let best = where === "end" ? { at: ex.length, rev: false } : where === "start" ? { at: 0, rev: false } : null;
    for (const rev of best ? [] : [false, true]) {
      const a = ll(rev ? blk[blk.length - 1] : blk[0]), b = ll(rev ? blk[0] : blk[blk.length - 1]);
      for (let at = 0; at <= ex.length; at++) {
        const prev = at > 0 ? ll(ex[at - 1]) : null, next = at < ex.length ? ll(ex[at]) : null;
        const cost = len + (prev ? dist(prev, a) : 0) + (next ? dist(b, next) : 0) - (prev && next ? dist(prev, next) : 0)
          + (rev ? 1e-6 : 0);   // Gleichstand: Richtung des Imports behalten
        if (!best || cost < best.cost) best = { cost, at, rev };
      }
    }
    const ins = (best.rev ? [...blk].reverse() : blk).map((p) => {
      const q = { ...p };
      if (best.rev && q.heading != null) q.heading = (q.heading + 180) % 360;
      return q;
    });
    // Start/Ziel des Blocks sind jetzt Zwischenpunkte; eigener Start/Ziel bleiben ohne „Nacht“
    const points = [...ex.slice(0, best.at), ...ins, ...ex.slice(best.at)].map((p) => ({ ...p }));
    // Anhaengen/Anfuegen: wo die beiden Teile zusammenstossen, endet eine Etappe
    const seam = where === "end" && ex.length ? ex.length - 1 : where === "start" && ex.length ? ins.length : -1;
    if (seam > 0 && seam < points.length - 1) { points[seam].stop = true; delete points[seam].stay; }
    if (points.length) { points[0].stop = false; points[points.length - 1].stop = false; }
    return { points, at: best.at, reversed: best.rev, added: ins.length };
  }

  const api = { pick, merge, trackFrom, cumKm, bearing, dist, MAX };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ImportPlan = api;
})(typeof window !== "undefined" ? window : globalThis);
