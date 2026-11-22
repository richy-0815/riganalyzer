/* Routenplaner: Punkte auf der Karte oder per Ortssuche setzen, eigener Valhalla-Dienst
   plant, die GPX laeuft danach durch die normale Analyse (uploadFile) und laesst sich herunterladen.
   Nutzt die Globals der jeweiligen Seite: map, $, esc, uploadFile – im Rig zusaetzlich veh. */
(function () {
  const RIG = typeof veh !== "undefined";
  const pts = [];                 // {lat, lon, name, stop}
  const layer = L.layerGroup().addTo(map);
  let line = null, picking = false, lastGpx = null, lastName = "geplante-route";
  let lastLegs = null, lastStayPts = null;   // Abschnitte + Punkt-Stand der letzten Planung (Fahrplan)
  let varLayer = null, varSeq = 0, varNote = "";   // Alternativrouten, s. loadVariants
  // Planen startet die Analyse nicht mehr selbst – dafuer der grosse Knopf „Analyse starten“.
  // analyzedGpx = die GPX, die zuletzt in die Analyse ging (anderer Stand -> „Analyse aktualisieren“).
  let analyzedGpx = null;
  // Analyse zur Tour speichern – plannerAnalysis = letzte fertige Analyse einer Planer-Route
  let plannerAnalysis = null, storedPending = null;
  let syncHook = () => {};
  let impHook = () => {};          // Knopf „Import einfuegen/uebernehmen“ je nach Planer-Stand (unten)
  let drag = null;                  // Umsortieren in der Liste: {i} = aktuelle Position des gezogenen Punkts          // gesetzt vom grossen Umschalter (weiter unten)
  // Umweltzonen (nur Rig): gemiedene Zonen-IDs, Schalter „Umweltzonen meiden“, Hinweis vom Server
  const avoided = new Set();
  // „Durchfahren“: Nacht-Vorschlag ausblenden – je Etappe/Nacht, bis sich die Punkte aendern
  const skipNights = new Set();
  let lezOn = false, lezNote = null, autoNext = false, autoCount = 0;
  let avoidToll = false, avoidFerry = false;   // Maut/Faehren meiden (nur Rig)
  let replanAfter = false;   // waehrend einer Planung umgeschaltet -> danach als „neu planen“ markieren
  const avoidBody = () => (RIG && (avoidToll || avoidFerry) ? { avoid: { toll: avoidToll, ferry: avoidFerry } } : {});
  // Abschnitts-Modi: pts[i].mode gilt fuer den Abschnitt BIS Punkt i (Start hat keinen)
  // Autobahn-Piktogramm statt 🛣 (static/autobahn.js) – Rig: ein Chip „mit/ohne“ je Abschnitt, Ride: drei Knoepfe
  const AB = window.Autobahn;
  const LEG_MODES = ["fast", "noway", "curvy"];
  const LEG_TITLE = { fast: "Mit Autobahn (schnell)", noway: "Ohne Autobahn", curvy: "Kleine Straßen & Kurven" };
  const legMode = (i) => pts[i].mode || $("pl-mode").value;
  // Autobahn ist Standard: nie eine alte Auswahl vom Browser wiederherstellen lassen
  $("pl-mode").value = "fast";

  // Start/Ziel als ovale Schilder, Zwischenpunkte als Nummern-Kreise
  const icon = (n, last, stop) => (n === 0 || last)
    ? L.divIcon({ className: "", iconSize: [58, 26], iconAnchor: [29, 13],
        html: `<div class="wp-pill ${n === 0 ? "start" : "ziel"}">${n === 0 ? "Start" : "Ziel"}</div>` })
    : L.divIcon({ className: "", iconSize: [24, 24], iconAnchor: [12, 12],
        html: `<div class="wp ${stop ? "stop" : ""}">${n}</div>` });
  const short = (p) => PlanText.label(p);
  // Aufenthalte zusammen (min) – nur Zwischenpunkte ohne Nacht, wie der Server sie zaehlt
  const stayTotal = () => pts.slice(1, -1).filter((p) => !p.stop).reduce((a, p) => a + (p.stay || 0), 0);
  function stayPick(p, i) {
    const opts = [...new Set([...Aufenthalt.OPTIONS, p.stay || 0])].sort((a, b) => a - b);
    return `<select class="stay" data-stay="${i}" aria-label="Aufenthalt an diesem Punkt" title="Aufenthalt hier – verschiebt Ankunft und Wetter im Fahrplan">${
      opts.map((m) => `<option value="${m}"${(p.stay || 0) === m ? " selected" : ""}>⏱ ${m ? Aufenthalt.fmt(m) : "–"}</option>`).join("")}</select>`;
  }
  const hm = (min) => `${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, "0")} h`;

  function draw() {
    layer.clearLayers();
    pts.forEach((p, i) => {
      L.marker([p.lat, p.lon], { icon: icon(i, i === pts.length - 1 && i > 0, p.stop), draggable: true, zIndexOffset: 900 })
        .on("dragend", (e) => {   // eigener Name bleibt, Vorschlag/Suchtreffer neu ermitteln
          const ll = e.target.getLatLng(); Object.assign(p, { lat: ll.lat, lon: ll.lng }); delete p.heading;
          PlanText.afterMove(p); resetLez(); draw(); suggestName(p);
        })
        .bindTooltip(esc(short(p))).addTo(layer);
    });
    if (pts.length > 1) L.polyline(pts.map((p) => [p.lat, p.lon]), { color: "#888", weight: 2, dashArray: "4 6", interactive: false }).addTo(layer);
    if (line) line.addTo(layer);
    else { clearNights(); clearVariants(); }   // Route ungueltig -> ihre Alternativen auch
    $("pl-list").innerHTML = pts.map((p, i) => (i === 0 ? "" : RIG
      ? `<li class="leg">${AB.legChip(i, legMode(i), `Abschnitt bis ${esc(short(p))}`)}</li>`
      : `<li class="leg"><span class="mini" role="group" aria-label="Abschnitt bis ${esc(short(p))}">${
      LEG_MODES.map((m) => `<button type="button" data-leg="${i}" data-mode="${m}" class="${legMode(i) === m ? "on" : ""}" title="${LEG_TITLE[m]}" aria-label="${LEG_TITLE[m]}" aria-pressed="${legMode(i) === m}">${AB.legLabel(m)}</button>`).join("")}</span></li>`)
      + `<li data-pt="${i}" class="${drag && drag.i === i ? "dragging" : ""}">${pts.length > 1
        ? `<button type="button" class="grip" data-grip="${i}" title="Ziehen zum Umsortieren (oder Pfeiltasten)" aria-label="${esc(short(p))} verschieben">⠿</button>` : ""}${i === 0 || (i === pts.length - 1 && i)
        ? `<span class="wp-pill sm ${i === 0 ? "start" : "ziel"}">${i === 0 ? "Start" : "Ziel"}</span>`
        : `<span class="wp ${p.stop ? "stop" : ""}">${i}</span>`}
      <span class="nm" data-rename="${i}" role="button" tabindex="0" title="${esc(short(p))} – antippen zum Umbenennen">${esc(short(p))}</span>
      <button type="button" class="x ren" data-rename="${i}" aria-label="Punkt umbenennen" title="Umbenennen">✎</button>
      ${RIG && i > 0 && i < pts.length - 1 ? `<label class="ov" title="Übernachtung: hier endet eine Etappe"><input type="checkbox" data-stop="${i}" ${p.stop ? "checked" : ""}> Nacht</label>` : ""}
      ${RIG && i > 0 && i < pts.length - 1 && !p.stop ? stayPick(p, i) : ""}
      ${RIG ? `<button type="button" class="x info" data-ptinfo="${i}" aria-label="Details und Karte zu diesem Punkt" title="Details und Karte">ℹ</button>` : ""}
      <button type="button" class="x" data-del="${i}" aria-label="Punkt entfernen">✕</button></li>`
      + (RIG ? `<li class="pl-detail" data-ptdetail="${i}" hidden></li>` : "")).join("");
    $("pl-list").classList.toggle("sorting", !!drag);
    if (openPt !== null && pts[openPt]) showPtInfo(openPt); else openPt = null;
    $("pl-go").disabled = pts.length < 2;
    $("pl-empty").hidden = pts.length > 0;
    syncHook();
    impHook();
    tourDirty();
  }

  // andere Punkte = andere Route: gemiedene Umweltzonen gelten nur fuers Neu-Planen derselben Punkte
  function resetLez() { avoided.clear(); lezNote = null; skipNights.clear(); }

  // Punkt von -> nach verschieben (Liste ziehen oder Pfeiltasten). Die betroffenen Abschnitte (zum Punkt, danach,
  // und die alte Luecke) fallen auf die Standard-Streckenart zurueck; Start/Ziel sind nie „Nacht“.
  function move(from, to) {
    if (to < 0 || to >= pts.length || to === from) return false;
    const after = pts[from + 1];
    const [p] = pts.splice(from, 1);
    pts.splice(to, 0, p);
    [p, after, pts[to + 1]].forEach((q) => { if (q) delete q.mode; });
    pts[0].stop = false; pts[pts.length - 1].stop = false;
    resetLez(); line = null; $("pl-result").hidden = true; $("pl-err").hidden = true;
    return true;
  }

  function add(lat, lon, name) {
    if (pts.length >= 25) return;
    const p = { lat, lon, name: name || "", stop: false };
    pts.push(p);
    resetLez(); line = null; $("pl-result").hidden = true; draw();
    if (!name) suggestName(p);
  }

  // Namensvorschlag fuer per Klick gesetzte/verschobene Punkte: „Wetzlar“ / „bei Gießen“ aus den
  // lokalen Kartendaten. Schlaegt das fehl, bleibt die Koordinate; eigene Namen werden nie ueberschrieben.
  let renaming = null;
  function suggestName(p) {
    if (p.custom || p.name) return;
    fetch(`/api/place?lat=${p.lat.toFixed(6)}&lon=${p.lon.toFixed(6)}`).then((r) => (r.ok ? r.json() : null)).then((j) => {
      if (j && pts.includes(p) && PlanText.applySuggestion(p, j.name)) { syncNaviName(p); if (renaming === null) draw(); }
    }).catch(() => {});
  }
  // Umbenennen nach dem Planen: „Ans Navi schicken“ nimmt die Namen vom Planungsstand – den passenden Eintrag nachziehen
  function syncNaviName(p) {
    const i = pts.indexOf(p);
    if (naviPts && naviPts.length === pts.length && i >= 0) naviPts[i].name = PlanText.stopName(p);
  }
  function startRename(i) {
    const p = pts[i], nm = $("pl-list").querySelector(`li[data-pt="${i}"] .nm`);
    if (!p || !nm) return;
    const input = document.createElement("input");
    Object.assign(input, { className: "nm-edit", maxLength: PlanText.NAME_MAX, value: p.name || "", placeholder: PlanText.coords(p) });
    input.setAttribute("aria-label", "Name des Punkts (leer = Koordinate)");
    nm.replaceWith(input);
    renaming = i;
    input.focus(); input.select();
    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true; renaming = null;
      if (save && input.value !== (p.name || "")) { PlanText.rename(p, input.value); syncNaviName(p); }
      draw();
    };
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
      else if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
  }

  function setPick(on) {
    picking = on;
    $("pl-pick").classList.toggle("on", on);
    $("pl-pick").textContent = on ? "✓ Klicks setzen Punkte" : "Punkte auf der Karte setzen";
    map.getContainer().classList.toggle("picking", on);
  }

  map.on("click", (e) => { if (picking) add(e.latlng.lat, e.latlng.lng); });

  // Platz aus „Uebernachten & Versorgen“ als Nacht-Punkt: dort einfuegen, wo der Umweg am kleinsten ist,
  // nie hinter das Ziel, dann sofort neu planen. false = keine geplante Route (z. B. hochgeladene GPX).
  window.planner = {
    // Fahrpausen der Tour: Fahrplan (rig.js) setzt/liest sie, gespeichert wird sie mit der Reise
    pause() { return tourPause; },
    setPause(v) { tourPause = v; tourDirty(); },
    addNight(p) {
      // nur wenn das Ergebnis gerade aus dem Planer kommt – sonst wuerde eine alte Planer-Route die GPX ersetzen
      if (!RIG || !(window.plannerResult || line) || pts.length < 2 || pts.length >= 25) return false;
      const at = CampFilter.placeNight(pts, p);
      const np = { lat: p.lat, lon: p.lon, name: p.name || "", stop: true };
      if (at.replace) pts[at.index] = { ...pts[at.index], ...np };   // Abschnitts-Modus bleibt
      else pts.splice(at.index, 0, np);
      line = null; draw();
      const d = $("pl-q").closest("details");
      if (d) d.open = true;
      $("pl-go").click();
      return true;
    },
  };
  if (RIG) Object.assign(window.planner, {
    points: () => pts.map((p) => ({ lat: p.lat, lon: p.lon, name: p.name || "", stop: !!p.stop, ...(p.poi ? { poi: p.poi } : {}) })),   // fuer e2e
    // Ort aus Einkaufen/Plaetze/Tankstellen/Rastplaetze als normaler Zwischenpunkt (kein Nacht-Stopp); danach „Route neu planen“ wie bei Platz-Aenderungen. false = kein Planer/voll
    addWaypoint(w) {
      if (!(window.plannerResult || line) || pts.length < 2 || pts.length >= 25) return false;
      pts.splice(CampFilter.insertIndex(pts, w), 0, { lat: w.lat, lon: w.lon, name: w.name || "", stop: false, poi: w.poi });
      draw(); needReplan();
      return true;
    },
    hasPoint: (p) => pts.some((q) => (p.osm && q.poi && q.poi.osm === p.osm) || (q.lat === p.lat && q.lon === p.lon)),
    // Aufenthalte je Etappe der zuletzt geplanten Route – Stand der Planung, nicht der ungeplanten Liste
    stays: () => (line && lastLegs && lastStayPts ? Aufenthalt.byStage(lastStayPts, lastLegs) : []),
    avoidedZones: () => [...avoided],
    lezNote: () => lezNote,
    // Knopf „umfahren“ im Ergebnis: Zonen dazunehmen und neu planen (nur Planer-Route)
    avoidZones(ids) {
      if (!(window.plannerResult || line) || pts.length < 2 || $("pl-go").disabled) return false;
      (ids || []).forEach((id) => avoided.add(id));
      $("pl-go").click();
      return true;
    },
    // nach jeder Analyse einer Planer-Route: Schalter an -> neue „no“-Zonen meiden (hoechstens 2x automatisch)
    lezAnalyzed(ids) {
      const fresh = (ids || []).filter((id) => !avoided.has(id));
      if (!lezOn || !fresh.length || autoCount >= 2 || $("pl-go").disabled) return;
      fresh.forEach((id) => avoided.add(id));
      needReplan();
    },
  });

  $("pl-list").addEventListener("click", (e) => {
    const rn = e.target.closest("[data-rename]");
    if (rn) { startRename(+rn.dataset.rename); return; }
    const lg = e.target.closest("[data-leg]");
    if (lg) {
      pts[+lg.dataset.leg].mode = lg.dataset.mode;
      draw();
      needReplan();
      return;
    }
    const d = e.target.closest("[data-del]");
    if (d) { pts.splice(+d.dataset.del, 1); resetLez(); line = null; draw(); }
  });
  $("pl-list").addEventListener("pointerdown", (e) => {
    const g = e.target.closest("[data-grip]");
    if (!g || e.button > 0) return;
    e.preventDefault();
    drag = { i: +g.dataset.grip };
    draw();
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag) return;
    e.preventDefault();
    // Ziel = Punktzeile, deren Mitte dem Zeiger am naechsten liegt
    let to = drag.i, best = Infinity;
    $("pl-list").querySelectorAll("li[data-pt]").forEach((r) => {
      const b = r.getBoundingClientRect(), d = Math.abs(e.clientY - (b.top + b.height / 2));
      if (d < best) { best = d; to = +r.dataset.pt; }
    });
    if (move(drag.i, to)) { drag.i = to; draw(); }
  }, { passive: false });
  const endDrag = () => {
    if (!drag) return;
    const i = drag.i;
    drag = null; draw();
    const g = $("pl-list").querySelector(`[data-grip="${i}"]`);
    if (g) g.focus({ preventScroll: true });
  };
  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);
  $("pl-list").addEventListener("keydown", (e) => {
    const rn = e.target.closest("span[data-rename]");
    if (rn && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); startRename(+rn.dataset.rename); return; }
    const g = e.target.closest("[data-grip]");
    if (!g || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    const from = +g.dataset.grip, to = from + (e.key === "ArrowUp" ? -1 : 1);
    if (!move(from, to)) return;
    draw();
    $("pl-list").querySelector(`[data-grip="${to}"]`)?.focus();
  });
  $("pl-list").addEventListener("change", (e) => {
    const s = e.target.closest("[data-stop]");
    if (s) { pts[+s.dataset.stop].stop = s.checked; draw(); }
    const sy = e.target.closest("[data-stay]");   // Aufenthalt: Punkt wird Halt (break) -> Route + Fahrplan neu
    if (sy) {
      const p = pts[+sy.dataset.stay], v = +sy.value;
      if (v > 0) p.stay = v; else delete p.stay;
      draw(); needReplan();
    }
  });
  $("pl-pick").addEventListener("click", () => setPick(!picking));
  $("pl-clear").addEventListener("click", () => {
    pts.length = 0; line = null; resetLez(); $("pl-result").hidden = true; draw();
    // Rig: lag eine importierte Tour darunter, wird sie wieder die aktive Route (mit voller Bewertung)
    const IR = window.importRoute;
    if (RIG && IR && IR.snap() && !IR.showing()) IR.restore();
  });
  $("pl-rev").addEventListener("click", () => { pts.reverse(); pts.forEach((p) => { delete p.mode; if (p.heading != null) p.heading = (p.heading + 180) % 360; }); resetLez(); line = null; draw(); });

  // Ortssuche: Treffer schon beim Tippen, Pfeiltasten + Enter, Esc schliesst, ✕ leert.
  // Treffer antippen = als nächsten Punkt (Ziel) anhängen, „als Start“ = vorne einfügen.
  const qIn = $("pl-q"), hitBox = $("pl-hits"), sBox = $("pl-sbox");
  let hits = [], sel = -1, seq = 0, tq = 0;
  const parts = (name) => {
    const a = String(name || "").split(",").map((x) => x.trim()).filter(Boolean);
    return [a[0] || "", a.slice(1).filter((x) => !/^\d{4,5}$/.test(x)).slice(0, 3).join(", ")];
  };
  function openHits(on) {
    hitBox.hidden = !on;
    qIn.setAttribute("aria-expanded", String(on));
    sBox.classList.toggle("open", on);
  }
  function msg(text) { hits = []; sel = -1; hitBox.innerHTML = `<li class="pl-hmsg">${text}</li>`; openHits(true); }
  function renderHits() {
    if (!hits.length) { msg("Nichts gefunden – anders schreiben oder Punkt auf der Karte setzen."); return; }
    hitBox.innerHTML = hits.map((h, i) => {
      const [main, rest] = parts(h.name);
      return `<li role="option" id="pl-hit-${i}" class="pl-hit${i === sel ? " sel" : ""}${h.in_area === false ? " far" : ""}" aria-selected="${i === sel}" data-hit="${i}">
        <span class="pin" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5a2.5 2.5 0 0 1 0-5 2.5 2.5 0 0 1 0 5z"/></svg></span>
        <span class="txt"><b>${esc(main)}</b>${rest ? `<small>${esc(rest)}</small>` : ""}${h.in_area === false
          ? '<small class="far-note">außerhalb des Planungsgebiets – nur als Punkt</small>' : ""}</span>
        ${pts.length ? `<button type="button" class="as-start" data-start="${i}" title="Als Start vorne einfügen">als Start</button>` : ""}
      </li>`;
    }).join("") + `<li class="pl-hfoot">Antippen = ${pts.length ? "als nächstes Ziel anhängen" : "als Start setzen"} · ↑↓ und Enter</li>`;
    if (sel >= 0) qIn.setAttribute("aria-activedescendant", `pl-hit-${sel}`); else qIn.removeAttribute("aria-activedescendant");
    openHits(true);
  }
  function take(i, atStart) {
    const h = hits[i];
    if (!h) return;
    const name = parts(h.name)[0];
    if (atStart && pts.length) {
      if (pts.length >= 25) return;
      pts.unshift({ lat: h.lat, lon: h.lon, name, stop: false });
      pts.forEach((p) => delete p.mode);
      resetLez(); line = null; $("pl-result").hidden = true; draw();
    } else add(h.lat, h.lon, name);
    map.setView([h.lat, h.lon], Math.max(map.getZoom(), 9));
    qIn.value = ""; $("pl-qclear").hidden = true; hits = []; sel = -1; openHits(false);
    qIn.focus();
  }
  async function search(now) {
    const q = qIn.value.trim();
    clearTimeout(tq);
    if (q.length < 2) { openHits(false); return; }
    if (!now) { tq = setTimeout(() => search(true), 450); return; }
    const my = ++seq;
    sBox.classList.add("busy");
    try {
      const r = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`);
      const j = await r.json();
      if (my !== seq) return;            // inzwischen weiter getippt
      if (!r.ok) throw new Error(j.detail || "Suche fehlgeschlagen");
      hits = j; sel = j.length ? 0 : -1;
      renderHits();
    } catch (err) {
      if (my === seq) msg(esc(err.message));
    } finally {
      if (my === seq) sBox.classList.remove("busy");
    }
  }
  qIn.addEventListener("input", () => { $("pl-qclear").hidden = !qIn.value; search(false); });
  qIn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!hits.length) return;
      e.preventDefault();
      sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + hits.length) % hits.length;
      renderHits();
      hitBox.querySelector(".pl-hit.sel")?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!hitBox.hidden && sel >= 0 && hits.length) take(sel, false);
      else search(true);
    } else if (e.key === "Escape") {
      if (!hitBox.hidden) { e.preventDefault(); openHits(false); }
    }
  });
  qIn.addEventListener("focus", () => { if (hits.length) renderHits(); });
  $("pl-search").addEventListener("click", () => search(true));
  $("pl-qclear").addEventListener("click", () => { qIn.value = ""; $("pl-qclear").hidden = true; hits = []; openHits(false); qIn.focus(); });
  hitBox.addEventListener("mousedown", (e) => e.preventDefault());   // Fokus bleibt im Feld
  hitBox.addEventListener("click", (e) => {
    const st = e.target.closest("[data-start]");
    if (st) { take(+st.dataset.start, true); return; }
    const li = e.target.closest("[data-hit]");
    if (li) take(+li.dataset.hit, false);
  });
  hitBox.addEventListener("mousemove", (e) => {
    const li = e.target.closest("[data-hit]");
    if (li && +li.dataset.hit !== sel) { sel = +li.dataset.hit; hitBox.querySelectorAll(".pl-hit").forEach((x) => x.classList.toggle("sel", +x.dataset.hit === sel)); }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".pl-searchwrap")) openHits(false); });

  /* Einstellung geaendert, Route schon geplant: nicht mehr von selbst neu planen – der Knopf zeigt
     „🔄 Route neu planen“ und ein Hinweis sagt es; geplant wird erst auf Knopfdruck. Ausdrueckliche Aktionen
     (Als Nacht, umfahren, Variante uebernehmen, Tour oeffnen) planen weiter sofort. */
  const staleHint = document.createElement("p");
  staleHint.className = "hint pl-stale";
  staleHint.id = "pl-stale";
  staleHint.hidden = true;
  staleHint.textContent = "Einstellung geändert – mit „Route neu planen“ übernehmen.";
  $("pl-go").after(staleHint);
  function needReplan() {
    if (!line) return;   // noch nicht geplant: gilt ohnehin fuer die naechste Planung
    if ($("pl-go").disabled) { replanAfter = true; return; }   // laeuft gerade -> danach markieren
    $("pl-go").classList.add("stale");
    $("pl-go").textContent = "🔄 Route neu planen";
    staleHint.hidden = false;
  }
  window.plannerNeedsReplan = () => !staleHint.hidden;

  $("pl-go").addEventListener("click", async () => {
    const btn = $("pl-go"), err = $("pl-err");
    btn.classList.remove("stale"); staleHint.hidden = true; replanAfter = false;
    err.hidden = true; btn.disabled = true; btn.textContent = "plane …";
    autoCount = autoNext ? autoCount + 1 : 0; autoNext = false;
    try {
      const nm = (p) => (p.name ? p.name.split(",")[0] : "");
      const a = nm(pts[0]), b = nm(pts[pts.length - 1]);
      const name = a && b ? `${a} – ${b}` : a ? `Ab ${a}` : b ? `Nach ${b}` : "Geplante Route";
      const reqBody = { points: pts.map(({ camp, poi, ...q }) => q), mode: $("pl-mode").value, vehicle: RIG ? veh : null, name,
        ...(RIG && avoided.size ? { avoid_zones: [...avoided] } : {}), ...avoidBody(),
        ...(nightLimit() ? { nights: { hours: nightLimit(), km: nightLimit() * 100 } } : {}) };
      const r = await fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reqBody) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Planung fehlgeschlagen");
      lastGpx = j.gpx; lastName = name.replace(/[^\wäöüÄÖÜß -]+/g, "").trim().replace(/\s+/g, "-") || "geplante-route";
      const ll = [];
      new DOMParser().parseFromString(j.gpx, "application/xml").querySelectorAll("trkpt")
        .forEach((p) => ll.push([+p.getAttribute("lat"), +p.getAttribute("lon")]));
      line = L.polyline(ll, { color: "#7048e8", weight: 4, opacity: 0.55, interactive: false });
      draw();
      if (ll.length) map.fitBounds(line.getBounds().pad(0.08));
      $("pl-sum").textContent = `${j.km.toLocaleString("de-DE")} km · ${hm(j.minutes)} reine Fahrzeit`
        + (j.stages.length > 1 ? ` · ${j.stages.length} Etappen` : "")
        // Aufenthalte an Zwischenpunkten – auch bei einer Etappe sichtbar (der Fahrplan kommt erst bei Reisen)
        + (RIG && stayTotal() ? ` · dazu ⏱ ${Aufenthalt.fmt(stayTotal())} Aufenthalt` : "")
        + (RIG ? ` · geplant als ${j.costing === "truck" ? (veh.trailer ? "Gespann" : "Lkw (über 3,5 t)") : "Pkw"} mit ${String(veh.height).replace(".", ",")} m Höhe` : "")
        + (j.note ? ` · ${j.note}` : "")
        + (RIG && avoided.size && !/nicht möglich/.test(j.lez_note || "") ? ` · ${avoided.size} Umweltzone${avoided.size > 1 ? "n" : ""} gemieden` : "")
        + (RIG && j.lez_note ? ` · ${j.lez_note}` : "")
        + (RIG && j.avoid_note ? ` · ${j.avoid_note}` : "")
        + (RIG && j.has_ferry && !avoidFerry ? " · mit Fähre" : "");
      lezNote = RIG ? j.lez_note || null : null;
      naviPts = pts.map((p) => ({ lat: p.lat, lon: p.lon, name: PlanText.stopName(p), stop: !!p.stop, mode: p.mode, ...(p.stay ? { stay: p.stay } : {}) }));
      lastLegs = j.legs || null;
      lastStayPts = pts.map((p) => ({ stop: !!p.stop, stay: p.stay || 0, name: PlanText.stopName(p) }));
      naviName = name;
      naviMode = $("pl-mode").value;
      if ($("pl-navi-st")) { $("pl-navi-st").textContent = ""; $("pl-navi-st").removeAttribute("data-state"); }
      $("pl-result").hidden = false;
      setPick(false);
      showNights(j.night_hints);
      if (j.too_long) {   // Analyse nimmt je Strecke/Etappe hoechstens max_km – GPX gibt es trotzdem
        err.textContent = RIG ? `Eine Etappe ist länger als ${j.max_km} km – setzt „Nacht“ an Zwischenpunkten${
          j.night_hints && j.night_hints.items.length ? " (Vorschläge unten)" : ""}, dann wird sie geprüft. Die GPX lässt sich trotzdem schon herunterladen.`
          : `Die Route ist länger als ${j.max_km} km – zum Bewerten bitte in Teilen planen. Die GPX lässt sich trotzdem herunterladen.`;
        err.hidden = false;
      }
      tooLong = !!j.too_long;
      analyzeState();
      anaNote("");
      if (storedPending) { const sp = storedPending; storedPending = null; useStored(sp); }
      if (RIG) loadVariants(reqBody);
    } catch (e) {
      err.textContent = e.message; err.hidden = false;
    } finally {
      btn.disabled = pts.length < 2; btn.textContent = "Route planen";
      if (replanAfter) { replanAfter = false; needReplan(); }
    }
  });

  /* Alternativrouten (nur Rig): nach dem Planen bis zu 3 Varianten (Valhalla-Alternativen bei
     Start -> Ziel, dazu „ohne/mit Autobahn“) als Liste und graue Linien. Antippen uebernimmt die Variante in die Planung –
     eine Valhalla-Alternative als Zwischenpunkt an der Stelle groesster Abweichung, damit Speichern und RigNavi genau
     sie fahren – und plant neu. */
  varLayer = L.layerGroup().addTo(map);
  function clearVariants() {
    varSeq++;
    if (varLayer) varLayer.clearLayers();
    const box = $("pl-variants");
    if (box) { box.hidden = true; box.innerHTML = ""; }
  }
  const signed = (n, unit) => `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(n).toLocaleString("de-DE")} ${unit}`;
  async function loadVariants(body) {
    const box = $("pl-variants");
    if (!box) return;
    const my = ++varSeq, note = varNote;
    varNote = "";
    varLayer.clearLayers();
    box.hidden = false;
    box.innerHTML = `${note ? `<p class="hint pl-var-note">${esc(note)}</p>` : ""}<span class="hint">Suche Alternativen …</span>`;
    try {
      const r = await fetch("/api/plan/variants", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      const j = await r.json();
      if (my !== varSeq) return;
      if (!r.ok) throw new Error(j.detail || "");
      const vs = j.variants || [], base = vs[0];
      const head = note ? `<p class="hint pl-var-note">${esc(note)}</p>` : "";
      if (vs.length < 2) { box.innerHTML = `${head}<span class="hint">Keine sinnvolle Alternative gefunden.</span>`; return; }
      const info = (v) => `${v.km.toLocaleString("de-DE")} km · ${hm(v.minutes)}`;
      // Zeit ab 60 min als Stunden wie die Fahrzeit daneben: „+10:54 h“ statt „+654 min“
      const diff = (v) => ` <span class="hint">(${signed(Math.round(v.km - base.km), "km")}, ${PlanText.signedTime(v.minutes - base.minutes)})</span>`;
      box.innerHTML = `${head}<b>Varianten</b> <span class="hint">– antippen zum Übernehmen</span><div class="pl-var-list">`
        + vs.map((v, i) => `<button type="button" class="pl-var${i ? "" : " on"}" data-var="${i}" aria-pressed="${!i}"${i ? "" : " disabled"}>`
          + `<b>${esc(v.label)}</b> ${info(v)}${i ? diff(v) : ""}</button>`).join("") + "</div>";
      vs.slice(1).forEach((v) => L.polyline(v.coords, { color: "#868e96", weight: 6, opacity: 0.55, dashArray: "8 8" })
        .bindTooltip(`${esc(v.label)}: ${info(v)} – antippen zum Übernehmen`, { sticky: true })
        .on("click", () => chooseVariant(v)).addTo(varLayer));
      box.querySelectorAll("[data-var]").forEach((b) => b.addEventListener("click", () => chooseVariant(vs[+b.dataset.var])));
      box.querySelectorAll("[data-var]").forEach((b) => {   // Linie beim Zeigen hervorheben
        const k = +b.dataset.var - 1;
        b.addEventListener("mouseenter", () => { const l = varLayer.getLayers()[k]; if (l) l.setStyle({ color: "#e8590c", opacity: 0.9 }); });
        b.addEventListener("mouseleave", () => { const l = varLayer.getLayers()[k]; if (l) l.setStyle({ color: "#868e96", opacity: 0.55 }); });
      });
    } catch {
      if (my === varSeq) box.innerHTML = `${note ? `<p class="hint pl-var-note">${esc(note)}</p>` : ""}<span class="hint">Alternativen gerade nicht verfügbar.</span>`;
    }
  }
  function chooseVariant(v) {
    if (!v || !v.apply || $("pl-go").disabled) return;
    if (v.apply.via && pts.length === 2) {
      const md = pts[1].mode;
      pts.splice(1, 0, { lat: v.apply.via.lat, lon: v.apply.via.lon, heading: v.apply.via.heading, name: v.label, stop: false,
        ...(md ? { mode: md } : {}) });
      varNote = `✓ ${v.label} übernommen – dafür steht ein Zwischenpunkt in der Liste. So bleibt sie beim Speichern und in RigNavi erhalten.`;
    } else if (v.apply.mode) {
      $("pl-mode").value = v.apply.mode;
      pts.forEach((p) => delete p.mode);   // gilt fuer alle Abschnitte
      varNote = `✓ ${v.label} übernommen (alle Abschnitte).`;
    } else return;
    line = null; draw();   // gemiedene Umweltzonen bleiben (kein resetLez)
    $("pl-go").click();
  }

  // Grosser Knopf: Analyse erst auf Wunsch. Erst „starten“, nach jeder Aenderung der Route „aktualisieren“.
  let tooLong = false;
  const anaBtn = $("pl-analyze");
  let prevAnalyzed = null;
  function analyzeState() {
    if (!anaBtn) return;
    anaBtn.hidden = !lastGpx || tooLong;
    const A = window.Analysis;
    anaBtn.classList.toggle("running", !!(A && A.running));
    if (A && A.running) {   // waehrend eine Analyse laeuft: nicht nochmal starten, nur abbrechen
      anaBtn.classList.remove("done");
      anaBtn.disabled = A.cancelled;
      anaBtn.textContent = A.cancelled ? "wird abgebrochen …" : "✕ Laufende Analyse abbrechen";
      anaBtn.title = "Erst abbrechen, dann mit dem aktuellen Stand neu starten.";
      return;
    }
    anaBtn.disabled = false;
    const fresh = analyzedGpx !== null && analyzedGpx === lastGpx;
    anaBtn.classList.toggle("done", fresh);
    anaBtn.textContent = analyzedGpx === null ? "▶ Analyse starten"
      : fresh ? "✓ Analyse ist aktuell – nochmal prüfen" : "🔄 Analyse aktualisieren";
    anaBtn.title = analyzedGpx !== null && !fresh ? "Die Route hat sich seit der letzten Analyse geändert." : "";
  }
  document.addEventListener("analysis-state", () => {
    const A = window.Analysis;
    if (A && !A.running && A.lastCancelled) analyzedGpx = prevAnalyzed;   // abgebrochen: gilt als nicht geprueft
    analyzeState();
  });
  if (anaBtn) anaBtn.addEventListener("click", () => {
    if (window.Analysis && window.Analysis.running) { window.Analysis.cancel(); return; }
    if (!lastGpx || tooLong) return;
    prevAnalyzed = analyzedGpx;
    analyzedGpx = lastGpx;
    analyzeState();
    uploadFile(new File([lastGpx], `${lastName}.gpx`, { type: "application/gpx+xml" }));
    window.plannerResult = true;   // „Als Uebernachtung uebernehmen“ darf diese Route aendern
  });
  // RigNavi: geplante Reise ans Handy schicken – der Server baut daraus das Offline-Paket
  // naviId: einmal geschickte Reise wird beim erneuten Schicken AKTUALISIERT (kein zweites 4-GB-Paket)
  let naviPts = null, naviName = "", naviMode = "fast", naviId = null, naviTimer = null;
  const naviBtn = $("pl-navi");
  if (naviBtn) naviBtn.addEventListener("click", async () => {
    if (!naviPts) return;
    const st = $("pl-navi-st");
    naviBtn.disabled = true; st.textContent = "wird geschickt …";
    try {
      const r = await fetch("/api/trips", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: naviId, name: naviName, vehicle: veh, mode: naviMode, stops: naviPts, ...avoidBody(),
          pause: tourPause || undefined }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Schicken fehlgeschlagen");
      naviId = j.trip.id;
      const id = naviId;
      if (RIG && !tourId) { tourId = id; $("pl-tname").value = j.trip.name; }
      naviBtn.textContent = "📱 Reise im Navi aktualisieren";
      // Balken + Schritte (static-rig/navistatus.js) statt Textzeile; ohne das Skript wie bisher Text
      const NS = window.NaviStatus;
      const show = (p) => {
        if (NS) NS.render(st, p, { onRetry: () => naviBtn.click() });
        else if (p.state === "ready") st.textContent = `✓ Paket fertig (${(p.size / 1e9).toLocaleString("de-DE", { maximumFractionDigits: 1 })} GB) – in RigNavi unter „Meine Reisen“ laden.`;
        else if (p.state === "error") st.textContent = `Paket fehlgeschlagen: ${p.msg}`;
        else st.textContent = `Paket wird gebaut: ${p.msg || "wartet"} (${p.progress || 0} %)`;
      };
      if (NS && j.package) show(j.package);
      else st.textContent = "An RigNavi geschickt – Paket wird gebaut (2–5 Min.). In der App unter „Meine Reisen“.";
      if (j.hint) st.append(Object.assign(document.createElement("div"), { className: "ns-sub", textContent: j.hint }));
      clearInterval(naviTimer);
      naviTimer = setInterval(async () => {
        const s = await fetch(`/api/trips/${id}`).then((x) => x.json()).catch(() => null);
        if (!s) return;
        const p = s.package;
        show(p);
        if (p.state === "ready" || p.state === "error" || p.state === "none") clearInterval(naviTimer);
      }, 5000);
    } catch (e) {
      st.textContent = e.message;
    } finally {
      naviBtn.disabled = false;
    }
  });
  // Nach dem Planen: Umschalter fuer die Streckenart direkt im Ergebnis – Antippen plant sofort neu.
  // Optionen = die des Auswahlfelds (Rig: mit/ohne Autobahn, Ride: schnell/ohne Autobahn/kurvig).
  const SWITCH_LABEL = { fast: "Autobahn erlaubt", noway: "Autobahn meiden", curvy: "Kurvig" };
  const sw = document.createElement("div");
  sw.className = "pl-switch";
  sw.setAttribute("role", "group");
  sw.setAttribute("aria-label", "Streckenart");
  [...$("pl-mode").options].forEach((o) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.mode = o.value;
    b.textContent = (RIG ? SWITCH_LABEL[o.value] : o.text.replace(/ \(.*\)/, "")) || o.text;
    b.addEventListener("click", () => {
      const mixed = pts.some((p, i) => i > 0 && p.mode && p.mode !== $("pl-mode").value);
      if (($("pl-mode").value === o.value && !mixed) || $("pl-go").disabled) return;
      $("pl-mode").value = o.value;
      pts.forEach((p) => delete p.mode);   // gilt fuer alle Abschnitte
      draw();
      needReplan();
    });
    sw.appendChild(b);
  });
  const mixedTag = document.createElement("span");
  mixedTag.className = "mixed";
  mixedTag.textContent = "gemischt";
  sw.appendChild(mixedTag);
  // Rig: ein Schalter „🛣 Autobahn“ – gruen „mit“, rot „ohne“, orange „gemischt“; Ride behaelt drei Knoepfe
  let mw = null;
  if (RIG) {
    sw.hidden = true;
    mw = document.createElement("button");
    mw.type = "button";
    mw.className = "mw-switch";
    mw.setAttribute("role", "checkbox");   // checkbox kennt aria-checked="mixed" (gemischt), switch nicht
    mw.innerHTML = `<span class="mw-lbl">${AB.pic()} Autobahn</span><span class="mw-state"></span><span class="mw-track"><span class="mw-thumb"></span></span>`;
    mw.addEventListener("click", () => {
      setAll();
      needReplan();
    });
  }
  // Alle Abschnitte auf einmal umschalten (gemischt -> mit); das versteckte #pl-mode haelt den Stand (Speichern, Planen)
  function abState() { return AB.state(pts.slice(1).map((_p, k) => legMode(k + 1)), $("pl-mode").value); }
  function setAll() {
    $("pl-mode").value = AB.nextAll(abState());
    pts.forEach((p) => delete p.mode);   // gilt fuer alle Abschnitte
    draw();
  }
  // Rig: Chip „Autobahn [Schild] mit | ohne | gemischt“ oben im Planer statt Auswahlfeld „Strecke“
  const abTop = RIG && $("pl-ab");
  if (abTop) {
    abTop.innerHTML = `${AB.pic()}<span class="ab-txt"></span>`;
    abTop.addEventListener("click", () => {
      setAll();
      needReplan();
    });
  }
  // Rig: Schalter „Umweltzonen meiden“ neben dem Autobahn-Schalter, Standard aus. An: Zonen, in die das
  // Fahrzeug laut Urteil nicht darf (aus der letzten Analyse), werden gemieden; aus: alles wieder erlaubt.
  let lz = null;
  if (RIG) {
    lz = document.createElement("button");
    lz.type = "button";
    lz.className = "mw-switch lez-switch";
    lz.setAttribute("role", "switch");
    lz.innerHTML = '<span class="mw-lbl">🌿 Umweltzonen meiden</span><span class="mw-state"></span><span class="mw-track"><span class="mw-thumb"></span></span>';
    lz.addEventListener("click", () => {
      lezOn = !lezOn;
      syncSwitch();
      if ($("pl-go").disabled) return;
      if (lezOn) {
        const fresh = (window.lezAvoidIds ? window.lezAvoidIds() : []).filter((id) => !avoided.has(id));
        if (!fresh.length || !window.plannerResult) return;
        fresh.forEach((id) => avoided.add(id));
        needReplan();
      } else if (avoided.size) {
        avoided.clear();
        needReplan();
      }
    });
  }
  // Maut / Faehren meiden: gelten fuer die ganze Tour, auch im Reisepaket fuer RigNavi
  const avoidSw = [];
  if (RIG) {
    [["toll", "💶 Maut meiden"], ["ferry", "⛴ Fähren meiden"]].forEach(([key, label]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `mw-switch avoid-switch avoid-${key}`;
      b.id = `pl-avoid-${key}`;
      b.setAttribute("role", "switch");
      b.innerHTML = `<span class="mw-lbl">${label}</span><span class="mw-state"></span><span class="mw-track"><span class="mw-thumb"></span></span>`;
      b.addEventListener("click", () => {
        if (key === "toll") avoidToll = !avoidToll; else avoidFerry = !avoidFerry;
        syncSwitch();
        if (tourBar) tourDirty();
        needReplan();
      });
      avoidSw.push([key, b]);
    });
  }
  function syncSwitch() {
    const modes = new Set(pts.slice(1).map((_p, k) => legMode(k + 1)));
    const mixed = modes.size > 1;
    sw.querySelectorAll("button").forEach((b) => b.classList.toggle("on", !mixed && b.dataset.mode === $("pl-mode").value));
    mixedTag.hidden = !mixed;
    const st = abState();
    [mw, abTop].forEach((el) => {
      if (!el) return;
      el.dataset.state = st;
      el.setAttribute("aria-checked", st === "mixed" ? "mixed" : String(st === "on"));
      el.querySelector(el === mw ? ".mw-state" : ".ab-txt").textContent = AB.TEXT[st];
    });
    avoidSw.forEach(([key, b]) => {
      const on = key === "toll" ? avoidToll : avoidFerry;
      b.dataset.state = on ? "on" : "off";
      b.setAttribute("aria-checked", String(on));
      b.querySelector(".mw-state").textContent = on ? "an" : "aus";
    });
    if (lz) {
      lz.dataset.state = lezOn ? "on" : "off";
      lz.setAttribute("aria-checked", String(lezOn));
      lz.querySelector(".mw-state").textContent = lezOn ? "an" : "aus";
    }
  }
  syncHook = syncSwitch;
  $("pl-mode").addEventListener("change", syncSwitch);
  $("pl-result").insertBefore(sw, $("pl-gpx"));
  if (mw) $("pl-result").insertBefore(mw, $("pl-gpx"));
  if (lz) $("pl-result").insertBefore(lz, $("pl-gpx"));
  avoidSw.forEach(([, b]) => $("pl-result").insertBefore(b, $("pl-gpx")));
  syncSwitch();

  $("pl-gpx").addEventListener("click", () => {
    if (!lastGpx) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lastGpx], { type: "application/gpx+xml" }));
    a.download = `${lastName}.gpx`; document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  });

  // ------------------------------------------------ Nachtplatz-Vorschlaege (nur Rig)
  // Ueberschreitet eine Etappe das Tageslimit (Fahrzeit oder km), schlaegt der Server passende Stell-/Campingplaetze
  // im Fenster um diese Stelle vor. „Als Nacht“ setzt den Platz als Uebernachtung ein und plant neu.
  const nightLayer = L.layerGroup().addTo(map);
  const nightBox = document.createElement("div");
  nightBox.className = "pl-nights";
  nightBox.hidden = true;
  $("pl-result").after(nightBox);
  const nightSel = $("pl-nights");
  try { const v = localStorage.getItem("plNights"); if (nightSel && v !== null && [...nightSel.options].some((o) => o.value === v)) nightSel.value = v; } catch { /* egal */ }
  if (nightSel) nightSel.addEventListener("change", () => {
    try { localStorage.setItem("plNights", nightSel.value); } catch { /* egal */ }
    needReplan();
  });
  function nightLimit() { return RIG && nightSel ? +nightSel.value || 0 : 0; }
  function clearNights() { nightLayer.clearLayers(); nightBox.hidden = true; nightBox.innerHTML = ""; }
  const KIND = { caravan_site: ["🚐", "Stellplatz"], camp_site: ["⛺", "Camping"] };
  const moon = L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13], html: '<div class="wp-night">🌙</div>' });
  let nightOpts = [], lastHints = null;
  const nightKey = (it) => `${it.stage}:${it.day || 1}`;
  function showNights(h) {
    clearNights();
    lastHints = h;
    if (!h || !h.items || !h.items.length) return;
    nightOpts = [];
    const many = h.items.some((it) => it.day > 1) || new Set(h.items.map((it) => it.stage)).size > 1;
    nightBox.innerHTML = `<h4>🌙 Übernachtung vorschlagen <span class="muted small">nach ${h.hours} h bzw. ${h.km} km Fahrt</span></h4>`
      + h.items.map((it) => {
        const head = `${many ? `Etappe ${it.stage}${it.day > 1 ? `, ${it.day}. Nacht` : ""}: ` : ""}nach ca. ${hm(it.at_min)} · ${Math.round(it.at_km)} km`;
        if (skipNights.has(nightKey(it))) {
          return `<div class="pl-night skipped"><div class="hd">${head} – <span class="muted">wird durchgefahren</span>
            <button type="button" class="linkbtn" data-unskip="${esc(nightKey(it))}">doch Vorschläge zeigen</button></div></div>`;
        }
        const rows = it.options.map((o) => {
          const k = nightOpts.push(o) - 1;
          const [ic, kind] = KIND[o.kind] || ["🏕", "Platz"];
          const f = o.flags || {};
          const extras = [f.dump && "Entsorgung", f.water && "Wasser", f.power && "Strom", f.fee === false && "kostenlos"].filter(Boolean);
          const lg = f.laenge, lgTxt = !lg ? "" : lg.kind === "none" ? " (ohne Längenbegrenzung)"
            : ` (${lg.level === "likely" ? "ca. " : "bis "}${String(lg.m).replace(".", ",")} m, ${lg.level === "likely" ? "geschätzt aus " : "laut "}${esc(lg.src)})`;
          const fit = o.fits === "ok" ? `<span class="fit ok">✓ passt${f.maxlength ? ` (bis ${String(f.maxlength).replace(".", ",")} m)` : lgTxt}</span>`
            : o.fits === "likely" ? `<span class="fit ok">✓ passt wahrscheinlich${lgTxt}</span>`
              : '<span class="fit unk">Maße nicht angegeben – vorher prüfen</span>';
          return `<li><span class="ic">${ic}</span><span class="nmw"><b>${esc(o.name)}</b>
            <span class="muted small">${kind} · ${String(o.off_km).replace(".", ",")} km abseits · nach ${hm(o.at_min)}${extras.length ? " · " + extras.join(", ") : ""}</span>
            ${fit}</span><span class="pl-acts">${typeof campDetails === "function" ? `<button type="button" class="ghost small" data-info="${k}" aria-expanded="false">ℹ Details</button>` : ""}`
            + `<button type="button" class="ghost small" data-night="${k}">Als Nacht</button></span></li><li class="pl-detail" data-detail="${k}" hidden></li>`;
        }).join("");
        const none = it.data ? "Kein passender Platz bis 5 km neben der Strecke – Punkt auf der Karte setzen."
          : "Für diese Gegend fehlen lokale Kartendaten – Punkt auf der Karte setzen.";
        return `<div class="pl-night"><div class="hd">${head}<button type="button" class="linkbtn" data-skip="${esc(nightKey(it))}"
          title="Keine Übernachtung hier – die Etappe wird am Stück gefahren">🚐💨 Durchfahren</button></div>${rows ? `<ul>${rows}</ul>` : `<p class="muted small">${none}</p>`}</div>`;
      }).join("");
    nightBox.hidden = false;
    // Monde nur fuer Naechte, die nicht durchgefahren werden
    const shownOpts = new Set(h.items.filter((it) => !skipNights.has(nightKey(it))).flatMap((it) => it.options));
    nightOpts.forEach((o, k) => shownOpts.has(o) && L.marker([o.lat, o.lon], { icon: moon, zIndexOffset: 800 })
      .bindTooltip(`${esc(o.name)} – als Nacht übernehmen`).on("click", () => takeNight(k)).addTo(nightLayer));
  }
  function takeNight(k) {
    const o = nightOpts[k];
    if (!o || pts.length < 2 || pts.length >= 25) return;
    // camp: der Vorschlag selbst – fuer „ℹ“ in der Punktliste (Platz-Details + Karte)
    pts.splice(CampFilter.insertIndex(pts, o), 0, { lat: o.lat, lon: o.lon, name: o.name, stop: true, camp: o });
    resetLez(); line = null; draw();
    $("pl-go").click();
  }
  nightBox.addEventListener("click", (e) => {
    const b = e.target.closest("[data-night]");
    if (b) { takeNight(+b.dataset.night); return; }
    const sk = e.target.closest("[data-skip], [data-unskip]");
    if (sk) {
      if (sk.dataset.skip) skipNights.add(sk.dataset.skip); else skipNights.delete(sk.dataset.unskip);
      showNights(lastHints);
      return;
    }
    const inf = e.target.closest("[data-info]");
    if (inf) toggleInfo(nightBox.querySelector(`[data-detail="${inf.dataset.info}"]`), inf, nightOpts[+inf.dataset.info]);
  });

  /* Details zu einem Platz: kleine eigene Karte mit Zoom auf den Platz (Karte/Luftbild umschaltbar)
     und dieselben Angaben wie im Karten-Popup – OSM, Tourismusamt, eigene Bewertungen, Einfahrt-Foto */
  const miniMaps = new WeakMap();
  // ℹ in der Punktliste: Karte mit Zoom auf den Punkt, bei einem uebernommenen Platz auch dessen Details
  let openPt = null;
  function showPtInfo(i) {
    const box = $("pl-list").querySelector(`[data-ptdetail="${i}"]`), p = pts[i];
    if (!box || !p) return;
    box.hidden = false;
    const btn = $("pl-list").querySelector(`[data-ptinfo="${i}"]`);
    if (btn) btn.setAttribute("aria-expanded", "true");
    if (p.camp && typeof campDetails === "function") {
      const c = campFromSuggestion(p.camp);
      box.innerHTML = `<div class="pl-detail-in"><div class="pl-mini"></div><div class="pl-info camppop">${campDetails(c)}</div></div>`;
      miniMap(box.querySelector(".pl-mini"), p.lat, p.lon, p.name);
      if (typeof loadMly === "function") loadMly(box.querySelector(".mlyph"));
      enrichCamp(c).then(() => { const info = box.querySelector(".pl-info"); if (info && pts[i] === p) { info.innerHTML = campDetails(c); loadMly(info.querySelector(".mlyph")); } });
    } else {
      const ll = `${(+p.lat).toFixed(5)}, ${(+p.lon).toFixed(5)}`;
      box.innerHTML = `<div class="pl-detail-in"><div class="pl-mini"></div><div class="pl-info"><b>${esc(short(p))}</b><br>`
        + `${typeof placeLinks === "function" ? placeLinks(p.lat, p.lon, short(p)) : esc(ll)}</div></div>`;
      miniMap(box.querySelector(".pl-mini"), p.lat, p.lon, short(p));
    }
  }
  $("pl-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-ptinfo]");
    if (!b) return;
    e.stopPropagation();
    const i = +b.dataset.ptinfo;
    if (openPt === i) { openPt = null; draw(); return; }
    openPt = i; draw();
  }, true);
  function toggleInfo(box, btn, o) {
    if (!box || !o) return;
    const open = box.hidden;
    box.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    btn.textContent = open ? "ℹ schließen" : "ℹ Details";
    if (!open || box.dataset.done) { if (open && miniMaps.has(box)) miniMaps.get(box).invalidateSize(); return; }
    box.dataset.done = "1";
    const p = campFromSuggestion(o);
    box.innerHTML = `<div class="pl-detail-in"><div class="pl-mini"></div><div class="pl-info camppop">${campDetails(p)}</div></div>`;
    miniMap(box.querySelector(".pl-mini"), o.lat, o.lon, o.name);
    if (typeof loadMly === "function") loadMly(box.querySelector(".mlyph"));
    enrichCamp(p).then(() => {
      const info = box.querySelector(".pl-info");
      if (!info) return;
      info.innerHTML = campDetails(p);
      if (typeof loadMly === "function") loadMly(info.querySelector(".mlyph"));
    });
  }
  function miniMap(el, lat, lon, name) {
    const m = L.map(el, { zoomControl: true, scrollWheelZoom: false, attributionControl: true }).setView([lat, lon], 16);
    const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap",
      referrerPolicy: "strict-origin-when-cross-origin" });
    // Luftbild ueber den eigenen Server (Esri sieht keine Nutzer-IP)
    const sat = L.tileLayer("/api/luftbild/{z}/{x}/{y}",
      { maxZoom: 19, attribution: "Luftbild © Esri, Maxar, Earthstar Geographics" });
    osm.addTo(m);   // Karte zuerst; Luftbild per Umschalter (Untergrund, Stellflaechen, Zufahrt)
    L.control.layers({ "Karte": osm, "Luftbild": sat }, null, { collapsed: false, position: "topright" }).addTo(m);
    // Google Maps hier einbetten erlauben Googles Bedingungen nur ueber deren kostenpflichtige Karten-API –
    // als Link oeffnet es dieselbe Stelle in Google Maps (Satellit, Street View von dort)
    const g = L.control({ position: "bottomleft" });
    g.onAdd = () => {
      const a = L.DomUtil.create("a", "pl-gmaps");
      a.href = `https://www.google.com/maps/@?api=1&map_action=map&center=${(+lat).toFixed(6)},${(+lon).toFixed(6)}&zoom=18&basemap=satellite`;
      a.target = "_blank"; a.rel = "noopener"; a.textContent = "Google Maps ↗";
      return a;
    };
    g.addTo(m);
    L.circleMarker([lat, lon], { radius: 8, color: "#fff", weight: 3, fillColor: "#17624f", fillOpacity: 1 }).bindTooltip(esc(name)).addTo(m);
    miniMaps.set(el.closest(".pl-detail"), m);
    setTimeout(() => m.invalidateSize(), 50);
  }

  /* ---------------------------------------------------------------- Meine Touren
     Speichern am Login auf dem Server (ohne Paketbau – das macht erst „Ans Navi schicken“), Öffnen, Kopie, frühere Stände,
     Löschen. Dazu ein Entwurf im Browser: bei Neuladen/Absturz lässt sich der ungespeicherte Stand zurückholen. */
  let tourId = null, tourName = "", tourDirtyFlag = false, tourLoading = false, draftT = 0, savedSnap = null, savedAt = "";
  let tourPause = null;   // auto | 2h20 | none (null = noch nicht gewaehlt -> Vorgabe des Browsers)
  const DRAFT = "plDraft";
  const tourBar = RIG ? document.createElement("div") : null;
  if (tourBar) {
    tourBar.className = "pl-tour";
    tourBar.innerHTML = `<input type="text" id="pl-tname" maxlength="80" placeholder="Name der Tour, z. B. Verdon 2027" autocomplete="off">
      <button type="button" class="ghost small" id="pl-tsave">💾 Speichern</button>
      <button type="button" class="ghost small" id="pl-topen" aria-expanded="false">📂 Meine Touren</button>
      <button type="button" class="ghost small" id="pl-tshare" title="Gespeicherte Tour mit einem anderen Login teilen">🔗 Teilen</button>
      <span class="hint" id="pl-tstate"></span>
      <div class="pl-draft" id="pl-draft" hidden></div>
      <div class="pl-tours" id="pl-tours" hidden></div>`;
    $("pl-list").before(tourBar);
  }
  function tourState() {
    return { v: 1, pts: pts.map((p) => ({ ...p })), mode: $("pl-mode").value, nights: nightSel ? nightSel.value : null,
      skip: [...skipNights], avoided: [...avoided], lez: lezOn, toll: avoidToll, ferry: avoidFerry, pause: tourPause };
  }
  function applyState(st) {
    tourLoading = true;
    pts.splice(0, pts.length, ...(st.pts || []).slice(0, 25));
    tourPause = st.pause || null;
    $("pl-mode").value = st.mode === "noway" ? "noway" : "fast";
    if (nightSel && st.nights != null && [...nightSel.options].some((o) => o.value === String(st.nights))) nightSel.value = String(st.nights);
    avoided.clear(); (st.avoided || []).forEach((z) => avoided.add(z));
    avoidToll = st.toll === true; avoidFerry = st.ferry === true;
    if (syncHook) syncHook();
    line = null; lezNote = null; $("pl-result").hidden = true; $("pl-err").hidden = true; clearNights();
    draw();
    skipNights.clear(); (st.skip || []).forEach((k) => skipNights.add(k));
    if (pts.length) map.fitBounds(L.latLngBounds(pts.map((p) => [p.lat, p.lon])).pad(0.15));
    tourLoading = false;
  }
  function setTourState(text) { if (tourBar) $("pl-tstate").textContent = text; }
  function tourDirty() {
    if (!tourBar || tourLoading) return;
    // Neu planen allein aendert nichts: nur echte Unterschiede zum gespeicherten Stand zaehlen
    if (tourId && savedSnap === JSON.stringify(tourState()) && $("pl-tname").value === tourName) {
      tourDirtyFlag = false; setTourState(savedAt); return;
    }
    tourDirtyFlag = true;
    setTourState(tourId ? "ungespeicherte Änderungen" : (pts.length ? "noch nicht gespeichert" : ""));
    clearTimeout(draftT);
    draftT = setTimeout(() => {
      try {
        if (pts.length) localStorage.setItem(DRAFT, JSON.stringify({ at: Date.now(), id: tourId, name: $("pl-tname").value, state: tourState() }));
        else localStorage.removeItem(DRAFT);
      } catch { /* privat/voll – nur Komfort */ }
    }, 1500);
  }
  function tourClean(t) {
    tourId = t.id; tourName = t.name; tourDirtyFlag = false;
    $("pl-tname").value = t.name;
    savedSnap = JSON.stringify(tourState());
    savedAt = `✓ gespeichert ${new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`;
    naviId = t.id;   // „Ans Navi schicken“ aktualisiert dieselbe Reise
    setTourState(savedAt);
    try { localStorage.removeItem(DRAFT); } catch { /* egal */ }
  }
  const tourStops = () => pts.map((p) => ({ lat: p.lat, lon: p.lon, name: PlanText.stopName(p), stop: !!p.stop, mode: p.mode, ...(p.stay ? { stay: p.stay } : {}) }));
  function autoName() {
    const nm = (p) => (p && p.name ? p.name.split(",")[0] : "");
    const a = nm(pts[0]), b = nm(pts[pts.length - 1]);
    return a && b ? `${a} – ${b}` : a || b || "Meine Tour";
  }
  async function saveTour(asNew = false) {
    if (pts.length < 2) { setTourState("Zum Speichern mindestens Start und Ziel setzen."); return; }
    const name = ($("pl-tname").value || "").trim() || autoName();
    setTourState("speichert …");
    try {
      const r = await fetch("/api/tours", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: asNew ? null : tourId, name, vehicle: veh, mode: $("pl-mode").value, stops: tourStops(), plan: tourState(),
          pause: tourPause || undefined }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `Fehler ${r.status}`);
      tourClean(j.trip);
      if (plannerAnalysis && plannerAnalysis.gpx === lastGpx) storeAnalysis();
      if (!$("pl-tours").hidden) listTours();
    } catch (e) { setTourState(`⚠ ${e.message}`); }
  }
  // ---- Analyse zur Tour: fertige Analyse einer Planer-Route wird mit der Tour gespeichert und beim
  // Oeffnen sofort gezeigt. Passt die neu geplante Route nicht mehr (Hash), heisst der Knopf „Analyse aktualisieren“.
  async function gpxHash(text) {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return [...new Uint8Array(buf)].slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  function anaNote(text) {
    const n = $("pl-ana-note");
    if (n) { n.textContent = text || ""; n.hidden = !text; }
  }
  async function storeAnalysis() {
    if (!RIG || !tourId || !plannerAnalysis || tourDirtyFlag) return;
    const pa = plannerAnalysis;
    try {
      const r = await fetch(`/api/trips/${tourId}/analysis`, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gpx_hash: await gpxHash(pa.gpx), result: pa.result }) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Fehler ${r.status}`);
      pa.stored = true;
      anaNote(`💾 Analyse mit der Tour gespeichert (${new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}) – beim nächsten Öffnen sofort da.`);
    } catch (e) { anaNote(`⚠ Analyse nicht gespeichert: ${e.message}`); }
  }
  document.addEventListener("analysis-done", (e) => {
    if (!RIG) return;
    // nur Analysen der aktuell geplanten Route gehoeren zur Tour (nicht hochgeladene GPX oder Beispiele)
    if (!window.plannerResult || !lastGpx || analyzedGpx !== lastGpx) { plannerAnalysis = null; return; }
    plannerAnalysis = { gpx: lastGpx, result: e.detail };
    if (tourId && !tourDirtyFlag) storeAnalysis();
    else anaNote("Analyse fertig – „💾 Speichern“ legt sie mit der Tour ab.");
  });
  async function useStored(a) {
    if (!a || !a.result || !window.showAnalysisResult) return;
    const same = lastGpx && (await gpxHash(lastGpx)) === a.gpx_hash;
    window.plannerResult = true;
    window.showAnalysisResult(a.result);
    analyzedGpx = same ? lastGpx : "(gespeichert)";
    plannerAnalysis = same ? { gpx: lastGpx, result: a.result, stored: true } : null;
    analyzeState();
    anaNote(`📂 Gespeicherte Analyse vom ${dt(a.saved)}`
      + (same ? " – passt zur Route." : " – die Route hat sich seitdem geändert (z. B. neue Kartendaten): „Analyse aktualisieren“ rechnet neu."));
  }

  async function openTour(id) {
    if (tourDirtyFlag && pts.length && !confirm("Ungespeicherte Änderungen verwerfen und die Tour öffnen?")) return;
    const j = await fetch(`/api/trips/${id}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (!j) { setTourState("⚠ Tour nicht gefunden."); return; }
    const t = j.trip;
    // aeltere Reisen (nur ans Navi geschickt) haben keinen Planer-Stand – dann aus den Zielen bauen
    applyState(t.plan || { pts: t.stops.map((s) => ({ ...s })), mode: t.mode });
    if (t.pause) tourPause = t.pause;   // gespeicherte Reise gewinnt (auch aeltere Plaene ohne pause)
    tourClean(t);
    // gespeicherte Analyse holen; eingespielt wird sie nach dem Planen (Hash-Vergleich mit der neuen Route)
    analyzedGpx = null; plannerAnalysis = null; anaNote("");
    storedPending = await fetch(`/api/trips/${id}/analysis`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    $("pl-tours").hidden = true; $("pl-topen").setAttribute("aria-expanded", "false");
    if (pts.length >= 2) $("pl-go").click();   // gleich neu planen (pruefen erst per Knopf)
  }
  // Zwei gespeicherte Touren zu einer Reise: die andere Tour hinten an die geoeffnete Planung.
  // Ziel A = Start B -> doppelter Punkt faellt weg (ImportPlan.merge), sonst plant der Planer das Verbindungsstueck.
  // Die Naht wird Etappen-Ende (Nacht), Naechte und Abschnitts-Einstellungen von B bleiben. Gespeichert wird erst per 💾.
  async function appendTour(id) {
    const j = await fetch(`/api/trips/${id}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (!j) { setTourState("⚠ Tour nicht gefunden."); return; }
    const t = j.trip, other = ((t.plan && t.plan.pts) || t.stops || []).map((p) => ({ ...p }));
    const m = ImportPlan.merge(pts, other, 25, "end");
    if (!m) { setTourState(pts.length >= 25 ? "⚠ Eure Planung hat schon 25 Punkte – mehr geht nicht." : "⚠ Nichts anzuhängen."); return; }
    applyState({ ...tourState(), pts: m.points });
    tourDirty();
    $("pl-tours").hidden = true; $("pl-topen").setAttribute("aria-expanded", "false");
    setTourState(`✓ „${t.name}“ angehängt (${m.added} Punkt${m.added > 1 ? "e" : ""}) – „${PlanText.label(m.points[m.at - 1])}“ ist jetzt Etappen-Ende. `
      + `Prüfen, dann „💾 Speichern“ – das schreibt in ${tourId ? "die geöffnete Tour (soll sie bleiben, vorher „Kopie“ öffnen)" : "eine neue Tour"}.`);
    if (pts.length >= 2) $("pl-go").click();
  }
  const dt = (s) => (s ? new Date(s).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" }) : "");
  async function listTours() {
    const box = $("pl-tours");
    box.innerHTML = `<p class="hint">lädt …</p>`;
    const list = await fetch("/api/trips").then((r) => (r.ok ? r.json() : [])).catch(() => []);
    box.innerHTML = list.length ? `<ul>${list.map((t) => `<li class="${t.id === tourId ? "cur" : ""}">
        <span class="tn"><b>${esc(t.name)}</b><small>${esc(t.from || "")} → ${esc(t.to || "")} · ${t.stages} Etappe${t.stages > 1 ? "n" : ""} · ${dt(t.updated)}${t.package && t.package.state === "ready" ? ` · 📱 Paket ${(t.package.size / 1e9).toLocaleString("de-DE", { maximumFractionDigits: 1 })} GB auf dem Server` : ""}${t.analysis ? " · 📊 Analyse gespeichert" : ""}</small></span>
        <span class="ta"><button type="button" class="ghost small" data-topen="${t.id}">Öffnen</button>
        <button type="button" class="ghost small" data-tcopy="${t.id}" title="Als neue Tour kopieren">Kopie</button>
        ${pts.length >= 2 && t.id !== tourId && window.ImportPlan ? `<button type="button" class="ghost small" data-tappend="${t.id}" title="Diese Tour hinten an eure geöffnete Planung hängen – zusammen eine Reise">➕ Anhängen</button>` : ""}
        <button type="button" class="ghost small" data-tver="${t.id}" title="Frühere Stände">🕘</button>
        ${t.package && t.package.state === "ready" ? `<button type="button" class="ghost small" data-tpkdel="${t.id}" title="Nur das Navi-Paket vom Server löschen (Platz auf dem Server) – die Tour bleibt">Paket löschen</button>` : ""}
        <button type="button" class="ghost small" data-tdel="${t.id}" title="Löschen">🗑</button></span>
        <div class="tver" data-tvbox="${t.id}" hidden></div></li>`).join("")}</ul>`
      : `<p class="hint">Noch keine gespeicherten Touren – oben planen und „💾 Speichern“.</p>`;
  }
  if (tourBar) {
    // „Im Planer öffnen“ aus „Mein Konto“ kommt als /?tour=<id> – Planer aufklappen, Tour laden, Adresse aufräumen
    const tq = new URLSearchParams(location.search).get("tour");
    if (tq && /^[0-9a-f]{12}$/.test(tq)) {   // wie trips.ID_RE
      history.replaceState(null, "", location.pathname);
      const card = document.getElementById("planner-card");
      if (card) { card.open = true; card.scrollIntoView({ block: "start" }); }
      setTimeout(() => openTour(tq), 0);
    }
    $("pl-tsave").addEventListener("click", () => saveTour());
    $("pl-tname").addEventListener("input", () => tourDirty());
    // geteilt wird der gespeicherte Stand – ungespeicherte Änderungen erst speichern
    $("pl-tshare").addEventListener("click", () => {
      if (!tourId || tourDirtyFlag || !window.Teilen) { $("pl-tstate").textContent = "Erst „💾 Speichern“ – geteilt wird die gespeicherte Tour."; return; }
      window.Teilen.open(tourId, $("pl-tname").value);
    });
    $("pl-topen").addEventListener("click", () => {
      const box = $("pl-tours"), open = box.hidden;
      box.hidden = !open; $("pl-topen").setAttribute("aria-expanded", String(open));
      if (open) listTours();
    });
    $("pl-tours").addEventListener("click", async (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.topen) openTour(b.dataset.topen);
      else if (b.dataset.tcopy) { await fetch(`/api/trips/${b.dataset.tcopy}/copy`, { method: "POST" }); listTours(); }
      else if (b.dataset.tappend) await appendTour(b.dataset.tappend);
      else if (b.dataset.tpkdel) {   // nur das Paket, die Tour bleibt
        if (!confirm("Navi-Paket vom Server löschen? Die Tour bleibt; aufs Handy schon geladene Pakete bleiben dort. Neu senden geht jederzeit.")) return;
        const r = await fetch(`/api/trips/${b.dataset.tpkdel}/package`, { method: "DELETE" });
        if (!r.ok) alert((await r.json().catch(() => ({}))).detail || "Löschen nicht möglich.");
        listTours();
      } else if (b.dataset.tdel) {
        if (!confirm("Tour löschen? Ein Offline-Paket dieser Reise im Navi wird ebenfalls gelöscht.")) return;
        await fetch(`/api/trips/${b.dataset.tdel}`, { method: "DELETE" });
        if (b.dataset.tdel === tourId) { tourId = null; naviId = null; setTourState("gelöscht – aktuelle Planung ist nicht mehr gespeichert"); }
        listTours();
      } else if (b.dataset.tver) {
        const vb = $("pl-tours").querySelector(`[data-tvbox="${b.dataset.tver}"]`);
        vb.hidden = !vb.hidden;
        if (vb.hidden) return;
        const j = await fetch(`/api/trips/${b.dataset.tver}/versions`).then((r) => r.json()).catch(() => ({ versions: [] }));
        vb.innerHTML = j.versions.length ? j.versions.map((v) => `<div>${dt(v.updated)} · ${esc(v.name || "")} · ${v.stops} Ziele
          <button type="button" class="linkbtn" data-trestore="${b.dataset.tver}" data-v="${v.v}">zurückholen</button></div>`).join("")
          : `<p class="hint">Noch keine früheren Stände.</p>`;
      } else if (b.dataset.trestore) {
        if (!confirm("Diesen früheren Stand zurückholen? Der jetzige bleibt als Version erhalten.")) return;
        const r = await fetch(`/api/trips/${b.dataset.trestore}/restore`, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ v: b.dataset.v }) });
        if (r.ok) { tourDirtyFlag = false; openTour(b.dataset.trestore); }
      }
    });
    // Entwurf von vorhin anbieten (nur wenn wirklich etwas drin ist)
    try {
      const d = JSON.parse(localStorage.getItem(DRAFT) || "null");
      if (d && d.state && d.state.pts && d.state.pts.length) {
        const box = $("pl-draft");
        box.innerHTML = `📝 Ungespeicherter Entwurf vom ${new Date(d.at).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}`
          + ` (${d.state.pts.length} Punkte${d.name ? `, „${esc(d.name)}“` : ""}) <button type="button" class="ghost small" data-draft="1">Wiederherstellen</button>`
          + ` <button type="button" class="linkbtn" data-draft="0">verwerfen</button>`;
        box.hidden = false;
        box.addEventListener("click", (e) => {
          const b = e.target.closest("[data-draft]");
          if (!b) return;
          if (b.dataset.draft === "1") {
            applyState(d.state); tourId = d.id || null; naviId = tourId; $("pl-tname").value = d.name || "";
            tourDirty(); if ($("planner-card")) $("planner-card").open = true;
          } else { try { localStorage.removeItem(DRAFT); } catch { /* egal */ } }
          box.hidden = true;
        });
      }
    } catch { /* kaputter Entwurf – ignorieren */ }
    // Seite verlassen mit ungespeicherten Aenderungen: der Entwurf ist sicher, trotzdem kurz nachfragen
    window.addEventListener("beforeunload", (e) => { if (tourDirtyFlag && tourId) { e.preventDefault(); e.returnValue = ""; } });
  }

  /* ------------------------------------------------ Eckdaten aus einem GPX-Import (nur Rig)
     Start, Ziel, Etappen-Enden („Nacht“) und Zwischenpunkte der importierten Tour in den Planer – geplant wird dann
     mit dem eigenen Fahrzeugprofil, die importierte Tour bleibt als Geisterstrecke zum Vergleich (static-rig/rig.js). */
  const impBox = RIG && $("pl-import-box");
  if (impBox && window.ImportPlan) {
    const IR = () => window.importRoute;
    const syncImp = () => { impBox.hidden = !(IR() && IR().snap()); };
    document.addEventListener("import-changed", () => { syncImp(); $("pl-import-st").textContent = DEFAULT_ST; });
    syncImp();
    // keine Rueckfrage (confirm) – unterdrueckt der Browser Dialoge, gilt sie stumm als „Abbrechen“ und der Knopf
    // wirkte tot. Stattdessen sofort uebernehmen und „↩ Rückgängig“ anbieten (alter Planer-Stand samt Tour-Bezug).
    let undo = null;
    const st = $("pl-import-st");
    const DEFAULT_ST = "Start, Ziel, Etappen und Zwischenpunkte der importierten Tour – danach mit eurem Fahrzeug neu planen.";
    // gibt es schon eine eigene Planung, wird der Import EINGEFUEGT (eigener Start/Ziel bleiben);
    // „alles ersetzen“ uebernimmt wie ohne Planung nur die Punkte des Imports.
    impHook = () => {
      const ins = pts.length > 0;
      $("pl-import").textContent = ins ? "📥 Import in Planung einfügen" : "📥 Eckdaten aus Import übernehmen";
      $("pl-import").title = ins ? "Eure Punkte bleiben – die Punkte der importierten Tour kommen an passender Stelle dazwischen" : "";
      $("pl-import-replace").hidden = !ins;
      // feste Stelle statt kleinstem Umweg – Etappe fuer Etappe aus mehreren GPX zusammensetzen
      $("pl-import-end").hidden = $("pl-import-start").hidden = pts.length < 2;
    };
    impHook();
    const nm = (p) => (p ? (p.name || "").split(",")[0] || `${(+p.lat).toFixed(3)}, ${(+p.lon).toFixed(3)}` : "");
    const pickBox = $("pl-import-pick");
    let cand = [];
    const closePick = () => { pickBox.hidden = true; pickBox.innerHTML = ""; $("pl-import-sel").setAttribute("aria-expanded", "false"); };
    // chosen: nur die in „Punkte auswählen …“ angehakten Punkte statt aller
    function doImport(replace, chosen, where = "auto") {
      try {
        const s = IR() && IR().snap();
        if (!s) { st.textContent = "Keine importierte Tour gefunden – bitte die GPX nochmal ablegen."; return; }
        const r = chosen
          ? { points: chosen.map((p, k) => ({ ...p, stop: p.stop && k > 0 && k < chosen.length - 1 })), source: "sel", cut: false }
          : ImportPlan.pick(s.track, { stageEnds: s.stageEnds, wpts: s.wpts, places: s.places, max: 25 });
        if (chosen && !chosen.length) { st.textContent = "Bitte mindestens einen Punkt anhaken."; return; }
        if (chosen && !pts.length && chosen.length < 2) { st.textContent = "Für eine neue Planung mindestens zwei Punkte anhaken (Start und Ziel)."; return; }
        if (r.points.length < (chosen ? 1 : 2)) { st.textContent = "Die importierte Tour ist zu kurz zum Übernehmen."; return; }
        closePick();
        const before = pts.length ? { state: tourState(), tourId, naviId, savedSnap, name: tourBar ? $("pl-tname").value : "" } : null;
        let msg;
        if (pts.length && !replace) {
          const m = ImportPlan.merge(pts, r.points, 25, where);
          if (!m) {
            st.textContent = pts.length >= 25 ? "Eure Planung hat schon 25 Punkte – mehr geht nicht."
              : "Nichts einzufügen – die Punkte der importierten Tour liegen schon in eurer Planung.";
            return;
          }
          undo = before;
          applyState({ ...tourState(), pts: m.points });
          if (tourBar) tourDirty();   // eigene Tour bleibt dieselbe (gleicher Name, gleiche gespeicherte Tour)
          const a = m.points[m.at - 1], b = m.points[m.at + m.added];
          const n = `${m.added} Punkt${m.added > 1 ? "e" : ""} der importierten Tour`;
          if (where === "end" || where === "start") msg = `✓ ${n} ${where === "end" ? `hinten angehängt – „${nm(a)}“` : `vorne angefügt – „${nm(b)}“`}`
            + " ist jetzt Etappen-Ende (Nacht). Dann „Route planen“.";
          else msg = `✓ ${n} eingefügt${a && b ? ` zwischen „${nm(a)}“ und „${nm(b)}“`
            : a ? ` hinter „${nm(a)}“` : b ? ` vor „${nm(b)}“` : ""}${m.reversed ? " (in umgekehrter Richtung)" : ""}. Euer Start und Ziel bleiben – dann „Route planen“.`;
        } else {
          undo = before;
          applyState({ pts: r.points, mode: $("pl-mode").value, nights: nightSel ? nightSel.value : null });
          if (tourBar) {   // neue Tour – eine geoeffnete gespeicherte Tour wird nicht ueberschrieben
            tourId = null; naviId = null; savedSnap = null;
            $("pl-tname").value = (s.name || "").slice(0, 80);
            tourDirty();
          }
          const vias = r.points.length - 2 - r.points.filter((p) => p.stop).length, nights = r.points.filter((p) => p.stop).length;
          msg = `✓ übernommen: Start, Ziel${nights ? `, ${nights} Nacht${nights > 1 ? "-Punkte" : "-Punkt"}` : ""}${vias
            ? `, ${vias} Zwischenpunkt${vias > 1 ? "e" : ""} ${r.source === "wpt" ? "aus den Wegpunkten der GPX" : r.source === "sel" ? "(ausgewählt)" : "entlang der Strecke"}` : ""}${
            r.cut ? " (gekürzt – höchstens 25 Punkte)" : ""}. Prüfen, verschieben oder löschen – dann „Route planen“.`;
        }
        setPick(false);
        st.textContent = msg;
        if (undo) {
          const u = document.createElement("button");
          u.type = "button"; u.className = "linkbtn"; u.id = "pl-import-undo";
          u.textContent = "↩ Rückgängig (vorherige Punkte)";
          st.append(" ", u);
        }
      } catch (e) {
        st.textContent = `⚠ Übernehmen ging nicht: ${e && e.message ? e.message : e}`;
        console.error(e);
      }
    }
    // „Punkte auswählen …“: alle Punkte des Imports als Haekchen-Liste; mit eigener Planung erst mal keiner angehakt
    $("pl-import-sel").addEventListener("click", () => {
      if (!pickBox.hidden) { closePick(); return; }
      const s = IR() && IR().snap();
      if (!s) { st.textContent = "Keine importierte Tour gefunden – bitte die GPX nochmal ablegen."; return; }
      cand = ImportPlan.pick(s.track, { stageEnds: s.stageEnds, wpts: s.wpts, places: s.places, max: 25 }).points;
      const on = pts.length ? "" : " checked";
      const tag = (p, i) => (i === 0 ? "Start" : i === cand.length - 1 ? "Ziel" : p.stop ? "Nacht" : "Zwischenpunkt");
      pickBox.innerHTML = `<div class="pip-head"><b>Punkte der importierten Tour</b>
        <button type="button" class="linkbtn" data-pip-all="1">alle</button><button type="button" class="linkbtn" data-pip-all="0">keine</button></div>
        <ul>${cand.map((p, i) => `<li><label><input type="checkbox" data-pip="${i}"${on}> <span class="pip-tag">${tag(p, i)}</span> ${esc(nm(p))}</label></li>`).join("")}</ul>
        <button type="button" class="ghost small" id="pl-import-take">✓ Ausgewählte ${pts.length ? "einfügen" : "übernehmen"}</button>`;
      pickBox.hidden = false;
      $("pl-import-sel").setAttribute("aria-expanded", "true");
    });
    pickBox.addEventListener("click", (e) => {
      const all = e.target.closest("[data-pip-all]");
      if (all) pickBox.querySelectorAll("input[data-pip]").forEach((c) => { c.checked = all.dataset.pipAll === "1"; });
      if (e.target.closest("#pl-import-take")) {
        const sel = [...pickBox.querySelectorAll("input[data-pip]:checked")].map((c) => cand[+c.dataset.pip]).filter(Boolean);
        doImport(false, sel);
      }
    });
    document.addEventListener("import-changed", closePick);
    $("pl-import").addEventListener("click", () => doImport(false));
    $("pl-import-replace").addEventListener("click", () => doImport(true));
    $("pl-import-end").addEventListener("click", () => doImport(false, null, "end"));
    $("pl-import-start").addEventListener("click", () => doImport(false, null, "start"));
    st.addEventListener("click", (e) => {
      if (!e.target.closest("#pl-import-undo") || !undo) return;
      const u = undo; undo = null;
      applyState(u.state);
      if (tourBar) { tourId = u.tourId; naviId = u.naviId; savedSnap = u.savedSnap; $("pl-tname").value = u.name; tourDirty(); }
      st.textContent = "↩ Vorherige Punkte wiederhergestellt.";
    });
  }

  fetch("/api/plan/status").then((r) => r.json()).then((s) => {
    $("pl-area").textContent = s.area;
    if (!s.ok) { $("pl-off").hidden = false; $("pl-go").dataset.off = "1"; }
  }).catch(() => {});
  draw();
})();

/* Handy im Modus "Desktop-Website": Seite wird winzig zweispaltig – kurzer Tipp oben */
(function () {
  try {
    const phone = window.matchMedia("(pointer: coarse)").matches && Math.min(screen.width, screen.height) < 600;
    if (!phone || window.innerWidth < 900 || sessionStorage.getItem("deskHint")) return;
    const d = document.createElement("div");
    d.className = "desk-hint";
    d.innerHTML = "📱 Tipp: Im Browser-Menü (⋮) „Desktop-Website“ ausschalten – dann passt die Seite aufs Handy.<button type=button>OK</button>";
    d.querySelector("button").onclick = () => { d.remove(); try { sessionStorage.setItem("deskHint", "1"); } catch {} };
    document.body.prepend(d);
  } catch { /* nur Komfort */ }
})();


