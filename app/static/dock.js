/* Ergebnis-Karten als Dashboard (Ride + Rig): jede Karte steckt in einem Rahmen mit
   ⠿ (ziehen), ↑/↓, ⇄ (andere Seite), ▾ (einklappen). Zwei Docks: links in der Spalte, rechts unter der Karte.
   Anordnung im Browser (localStorage) und am Login. Reine Funktionen auch fuer node --test;
   im Browser window.DockKit (DockKit.mount({...})). */
(function (root) {
  // Standard-Anordnungen je App (Ride: Streckenband unter der Karte)
  const DEFAULTS = {
    rig: { left: ["upload", "planner", "vehicle", "result", "verdict", "fuel", "profile", "list", "rules", "quality"],
           right: ["trip"], collapsed: [] },
    ride: { left: ["upload", "planner", "result", "profiles", "highlights", "weather", "list", "quality", "compare"],
            right: ["band"], collapsed: [] },
  };

  const list = (v) => (Array.isArray(v) ? v : []);
  /** Gemerkte Anordnung auf die vorhandenen Karten (present) anwenden: Unbekanntes/Doppeltes raus,
   *  neue Karten an ihren Standardplatz – vor die naechste bekannte im selben Dock, sonst ans Ende. */
  function arrange(saved, def, present) {
    const s = saved && typeof saved === "object" ? saved : {};
    const has = new Set(present), seen = new Set();
    const pick = (ids) => list(ids).filter((id) => {
      if (!has.has(id) || seen.has(id)) return false;
      seen.add(id); return true;
    });
    const out = { left: pick(s.left), right: pick(s.right) };
    for (const side of ["left", "right"]) {
      const ids = def[side], arr = out[side];
      ids.forEach((id, i) => {
        if (seen.has(id) || !has.has(id)) return;
        const next = ids.slice(i + 1).find((n) => arr.includes(n));
        if (next) arr.splice(arr.indexOf(next), 0, id); else arr.push(id);
        seen.add(id);
      });
    }
    const coll = Array.isArray(s.left) || Array.isArray(s.right) ? list(s.collapsed) : list(def.collapsed);
    out.collapsed = coll.filter((id, i, a) => has.has(id) && a.indexOf(id) === i);
    return out;
  }
  function isDefault(lay, def) {
    const same = (a, b) => list(a).length === list(b).length && list(a).every((x, i) => x === list(b)[i]);
    return same(lay.left, def.left) && same(lay.right, def.right) && same(lay.collapsed, def.collapsed);
  }
  /** Zwei gemerkte Staende (mit at): der juengere gewinnt, ohne Zeitstempel gilt er als aelter. */
  function newer(a, b) {
    if (!a) return b || null;
    if (!b) return a;
    return (a.at || 0) > (b.at || 0) ? a : b;
  }

  const api = { DEFAULTS, arrange, isDefault, newer };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  /* ---------------------------------------------------------------- Seite
     opts: left/right (ids der Docks), def (Standard), key (localStorage), result (id: versteckt = keine Ergebnis-Karten),
     map (() => Leaflet-Karte zum Neu-Ausmessen), reset (id des Zuruecksetzen-Knopfs), resetAuto (Knopf nur, wenn nicht
     Standard), onChange (nach jeder Aenderung, Rig: saveUi), part (prefs-Bereich: Stand mit at, der neuere gewinnt) */
  function mount(opts) {
    const $ = (id) => document.getElementById(id);
    const L = $(opts.left), R = $(opts.right), DEF = opts.def;
    const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
    const items = {};
    document.querySelectorAll("[data-card]").forEach((card) => {
      const id = card.dataset.card, w = document.createElement("div");
      w.className = "dock-item"; w.dataset.id = id;
      w.innerHTML = `<div class="dock-tools" role="toolbar" aria-label="${esc(card.dataset.title || id)} anordnen">
      <button type="button" class="grab" data-act="grab" title="Ziehen zum Verschieben" aria-label="verschieben">⠿</button>
      <button type="button" data-act="up" title="nach oben" aria-label="nach oben">↑</button>
      <button type="button" data-act="down" title="nach unten" aria-label="nach unten">↓</button>
      <button type="button" data-act="side" title="auf die andere Seite (links ↔ unter der Karte)" aria-label="andere Seite">⇄</button>
      <button type="button" data-act="fold" title="einklappen" aria-label="einklappen">▾</button></div>`;
      card.before(w); w.appendChild(card);
      items[id] = w;
      new MutationObserver(refresh).observe(card, { attributes: true, attributeFilter: ["hidden"] });
    });
    const resultEl = $(opts.result);
    new MutationObserver(() => refresh()).observe(resultEl, { attributes: true, attributeFilter: ["hidden"] });
    const resetBtn = opts.reset && $(opts.reset);

    const read = () => { try { return JSON.parse(localStorage.getItem(opts.key) || "null"); } catch { return null; } };
    let saved = read();
    let cur = saved || DEF;

    function apply(lay) {
      const st = arrange(lay, DEF, Object.keys(items));
      st.left.forEach((id) => L.appendChild(items[id]));
      st.right.forEach((id) => R.appendChild(items[id]));
      Object.values(items).forEach((w) => w.classList.toggle("collapsed", st.collapsed.includes(w.dataset.id)));
      cur = lay; refresh();
    }
    function layout() {
      const ids = (d) => [...d.children].filter((w) => w.dataset.id).map((w) => w.dataset.id);
      return { left: ids(L), right: ids(R), collapsed: Object.values(items).filter((w) => w.classList.contains("collapsed")).map((w) => w.dataset.id) };
    }
    let timer = null;
    function save() {
      cur = layout();
      const st = opts.part ? { ...cur, at: Date.now() } : cur;
      if (opts.part) saved = st;
      try { localStorage.setItem(opts.key, JSON.stringify(st)); } catch { /* privat */ }
      if (opts.part) {
        clearTimeout(timer);
        timer = setTimeout(() => {
          const csrf = (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
          fetch("/api/me/prefs", { method: "PUT", headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf },
            body: JSON.stringify({ [opts.part]: st }) }).catch(() => {});
        }, 500);
      }
      if (opts.onChange) opts.onChange(cur);
    }
    function refresh() {
      const noResult = resultEl.hidden;   // Ergebnis-Karten erst nach einer Analyse
      Object.values(items).forEach((w) => w.classList.toggle("empty", !!w.lastElementChild.hidden
        || (w.lastElementChild.dataset.result === "1" && noResult)));
      const rightShown = [...R.children].some((w) => !w.classList.contains("empty"));
      R.hidden = !rightShown;
      if (resetBtn && opts.resetAuto) resetBtn.hidden = isDefault(layout(), DEF);
      // Karte/Profil neu ausmessen (Breite/Hoehe hat sich evtl. geaendert)
      requestAnimationFrame(() => { const m = opts.map && opts.map(); if (m) m.invalidateSize(); window.dispatchEvent(new Event("resize")); });
    }
    document.addEventListener("click", (e) => {
      const b = e.target.closest && e.target.closest(".dock-tools button");
      if (!b) return;
      const w = b.closest(".dock-item"), dock = w.parentElement;
      const sib = (dir) => { let n = dir < 0 ? w.previousElementSibling : w.nextElementSibling;
        while (n && n.classList.contains("empty")) n = dir < 0 ? n.previousElementSibling : n.nextElementSibling; return n; };
      if (b.dataset.act === "up") { const n = sib(-1); if (n) n.before(w); }
      else if (b.dataset.act === "down") { const n = sib(1); if (n) n.after(w); }
      else if (b.dataset.act === "side") (dock === L ? R : L).appendChild(w);
      else if (b.dataset.act === "fold") w.classList.toggle("collapsed");
      else return;
      refresh(); save();
      if (b.dataset.act === "side") w.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    // Ziehen (Maus): ⠿ anfassen -> ganzer Rahmen wird ziehbar; beim Ziehen live einsortieren
    let dragging = null;
    document.addEventListener("pointerdown", (e) => {
      const g = e.target.closest && e.target.closest(".dock-tools .grab");
      if (g) g.closest(".dock-item").draggable = true;
    });
    [L, R].forEach((dock) => {
      dock.addEventListener("dragstart", (e) => {
        dragging = e.target.closest(".dock-item"); if (!dragging) return;
        dragging.classList.add("dragging"); e.dataTransfer.effectAllowed = "move";
        try { e.dataTransfer.setData("text/plain", dragging.dataset.id); } catch { /* egal */ }
        R.hidden = false; L.classList.add("drop-on"); R.classList.add("drop-on");
      });
      dock.addEventListener("dragover", (e) => {
        if (!dragging) return;
        e.preventDefault();
        const after = [...dock.children].filter((w) => w !== dragging && !w.classList.contains("empty"))
          .find((w) => { const r = w.getBoundingClientRect(); return e.clientY < r.top + r.height / 2; });
        if (after) after.before(dragging); else dock.appendChild(dragging);
      });
      dock.addEventListener("drop", (e) => e.preventDefault());
      dock.addEventListener("dragend", () => {
        if (!dragging) return;
        dragging.classList.remove("dragging"); dragging.draggable = false; dragging = null;
        L.classList.remove("drop-on"); R.classList.remove("drop-on");
        refresh(); save();
      });
    });
    if (resetBtn) resetBtn.addEventListener("click", () => { apply(DEF); save(); });
    apply(cur);
    // Stand vom Login: juenger als der im Browser? dann gilt er (anderes Geraet)
    if (opts.part) {
      fetch("/api/me/prefs").then((r) => (r.ok ? r.json() : {})).then((p) => {
        const best = newer(p && p[opts.part], saved && saved.at ? saved : null);
        if (best && best !== saved) {
          saved = best; apply(best);
          try { localStorage.setItem(opts.key, JSON.stringify(best)); } catch { /* privat */ }
        }
      }).catch(() => {});
    }
    return { layout, apply, refresh };
  }
  root.DockKit = { ...api, mount };
})(typeof window !== "undefined" ? window : globalThis);
