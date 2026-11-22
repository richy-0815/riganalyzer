/* Verschiebbare Kacheln („Mein Konto“ und Admin-Dashboard).
   Eine Spalte (Konto, Admin am Handy) oder zwei Spalten (Admin): ⠿ ziehen, ↑/↓, ⇄ Spalte, ⇔ halb/ganz breit, ▾ einklappen.
   Anordnung im Browser (localStorage) und am Login (prefs, der neuere Stand gewinnt).
   Reine Funktionen auch fuer node --test; im Browser window.Cards (Cards.mount({...})). */
(function (root) {
  /** Gemerkte Reihenfolge auf die vorhandenen Kacheln anwenden; fehlende an ihren Standardplatz
   *  (vor die naechste bekannte, wie rig.js Dock.apply). */
  function arrange(saved, defaults) {
    const out = (Array.isArray(saved) ? saved : []).filter((id, i, a) => defaults.includes(id) && a.indexOf(id) === i);
    defaults.forEach((id, i) => {
      if (out.includes(id)) return;
      const next = defaults.slice(i + 1).find((n) => out.includes(n));
      if (next) out.splice(out.indexOf(next), 0, id); else out.push(id);
    });
    return out;
  }
  function move(order, id, dir) {
    const o = order.slice(), i = o.indexOf(id), j = i + dir;
    if (i < 0 || j < 0 || j >= o.length) return o;
    [o[i], o[j]] = [o[j], o[i]];
    return o;
  }
  function newer(a, b) {
    if (!a) return b || null;
    if (!b) return a;
    return (a.at || 0) > (b.at || 0) ? a : b;
  }

  /** Vollstaendiger Stand aus Gemerktem + Standard (defs: {order, wide, col}). Breite/Spalte gelten nur fuer Kacheln,
   *  die der gemerkte Stand kennt (in seiner order) – neue/andere bekommen ihren Standard. */
  function norm(saved, defs) {
    const s = saved || {}, known = Array.isArray(s.order) ? s.order : [];
    const sw = Array.isArray(s.wide) ? s.wide : [], sc = s.col && typeof s.col === "object" ? s.col : {};
    const order = arrange(known, defs.order);
    const dw = defs.wide || [], dc = defs.col || {};
    const col = {};
    order.forEach((id) => {
      col[id] = known.includes(id) && (sc[id] === "l" || sc[id] === "r") ? sc[id] : dc[id] === "r" ? "r" : "l";
    });
    return {
      order,
      // eingeklappt: gemerkter Stand fuer bekannte Kacheln, sonst Standard (data-fold, z. B. „Versionen“)
      collapsed: order.filter((id) => (known.includes(id) ? (Array.isArray(s.collapsed) ? s.collapsed : [])
        : defs.collapsed || []).includes(id)),
      wide: order.filter((id) => (known.includes(id) ? sw : dw).includes(id)),
      col,
    };
  }
  /** Zeilen fuer zwei Spalten: {wide: id} oder {l: [...], r: [...]} (aufeinanderfolgende halbe Kacheln). */
  function rows(st) {
    const out = [];
    let seg = null;
    st.order.forEach((id) => {
      if (st.wide.includes(id)) { out.push({ wide: id }); seg = null; return; }
      if (!seg) { seg = { l: [], r: [] }; out.push(seg); }
      seg[st.col[id] === "r" ? "r" : "l"].push(id);
    });
    return out;
  }
  const clone = (st) => ({ ...st, order: st.order.slice(), collapsed: st.collapsed.slice(), wide: st.wide.slice(),
    col: { ...st.col } });
  const rowIds = (r) => (r.wide ? [r.wide] : r.l.concat(r.r));
  function insert(order, id, ref, after) {
    const o = order.filter((x) => x !== id);
    if (ref == null || !o.includes(ref)) { o.push(id); return o; }
    o.splice(o.indexOf(ref) + (after ? 1 : 0), 0, id);
    return o;
  }
  /** ↑ (dir -1) / ↓ (+1) in zwei Spalten: halbe Kachel innerhalb ihrer Spalte, am Rand ueber die breite Nachbarin;
   *  breite Kachel springt ueber eine ganze Zeile. */
  function step(st, id, dir) {
    const out = clone(st), i = st.order.indexOf(id);
    if (i < 0) return out;
    if (st.wide.includes(id)) {
      const rs = rows(st), k = rs.findIndex((r) => r.wide === id), n = rs[k + dir];
      if (!n) return out;
      const ids = rowIds(n).sort((a, b) => st.order.indexOf(a) - st.order.indexOf(b));
      out.order = insert(st.order, id, dir < 0 ? ids[0] : ids[ids.length - 1], dir > 0);
      return out;
    }
    for (let j = i + dir; j >= 0 && j < st.order.length; j += dir) {
      const o = st.order[j];
      if (st.wide.includes(o) || st.col[o] === st.col[id]) {
        out.order = insert(st.order, id, o, dir > 0);
        return out;
      }
    }
    return out;
  }
  /** ⇔ (what "wide"), ▾ ("collapsed") umschalten; ⇄ ("col") wechselt die Spalte. */
  function toggle(st, what, id) {
    const out = clone(st);
    if (what === "col") out.col[id] = st.col[id] === "r" ? "l" : "r";
    else out[what] = st[what].includes(id) ? st[what].filter((x) => x !== id) : st.order.filter((x) => x === id || st[what].includes(x));
    return out;
  }
  /** Ziehen: id vor (after=false) / hinter ref, ohne ref ans Ende; halbe Kachel nimmt col an. */
  function place(st, id, ref, after, col) {
    const out = clone(st);
    if (id === ref || !st.order.includes(id)) return out;
    out.order = insert(st.order, id, ref, after);
    if (col && !st.wide.includes(id)) out.col[id] = col;
    return out;
  }
  /** Was gespeichert wird (ohne at); eine Spalte (Konto) nur Reihenfolge + eingeklappt. */
  function snapshot(st, two = true) {
    const s = { order: st.order.slice(), collapsed: st.collapsed.slice() };
    if (two) { s.wide = st.wide.slice(); s.col = { ...st.col }; }
    return s;
  }

  const api = { arrange, move, newer, norm, rows, step, toggle, place, snapshot };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  /* ---------------------------------------------------------------- Seite
     opts: box (id), attr (data-Attribut der Kacheln, z. B. "kcard"), key (localStorage), part (prefs-Bereich),
     reset (id des Zuruecksetzen-Knopfs), two (Media-Query fuer zwei Spalten; ohne = immer eine Spalte) */
  function mount(opts) {
    const box = document.getElementById(opts.box);
    if (!box) return null;
    const twoCap = !!opts.two;
    const mq = twoCap && root.matchMedia ? root.matchMedia(opts.two) : null;
    const items = {}, defs = { order: [], wide: [], col: {}, collapsed: [] };
    const tools = (twoCap ? [["wide", "⇔", "ganz breit"], ["col", "⇄", "andere Spalte"]] : [])
      .map(([a, t, l]) => `<button type="button" data-act="${a}" title="${l}" aria-label="${l}">${t}</button>`).join("");
    box.querySelectorAll(`:scope > [data-${opts.attr}]`).forEach((card) => {
      const id = card.dataset[opts.attr], w = document.createElement("div");
      w.className = "kt-item"; w.dataset.id = id;
      w.innerHTML = `<div class="kt-dock" role="toolbar">
        <button type="button" class="grab" data-act="grab" title="Ziehen zum Verschieben" aria-label="verschieben">⠿</button>
        <button type="button" data-act="up" title="nach oben" aria-label="nach oben">↑</button>
        <button type="button" data-act="down" title="nach unten" aria-label="nach unten">↓</button>${tools}
        <button type="button" data-act="fold" title="einklappen" aria-label="einklappen">▾</button></div>`;
      w.firstElementChild.setAttribute("aria-label", `${card.dataset.title || id} anordnen`);
      card.before(w); w.appendChild(card);
      items[id] = w;
      defs.order.push(id);
      if (card.dataset.wide === "1") defs.wide.push(id);
      if (card.dataset.fold === "1") defs.collapsed.push(id);
      defs.col[id] = card.dataset.col === "r" ? "r" : "l";
    });
    const two = () => !!(mq && mq.matches);

    const read = () => { try { return JSON.parse(localStorage.getItem(opts.key) || "null"); } catch { return null; } };
    let saved = read();
    let cur = norm(saved, defs);

    function render(st) {
      cur = st;
      box.classList.toggle("two", two());
      const wrap = (cls, kids, col) => {
        const d = document.createElement("div");
        d.className = cls; d.append(...kids);
        if (col) d.dataset.col = col;
        return d;
      };
      const colOf = (r, c) => wrap("kt-col", r[c].map((id) => items[id]), c);
      box.replaceChildren(...(two()
        ? rows(st).map((r) => (r.wide ? items[r.wide] : wrap("kt-row", [colOf(r, "l"), colOf(r, "r")])))
        : st.order.map((id) => items[id])));
      defs.order.forEach((id) => {
        const w = items[id], fold = st.collapsed.includes(id), wide = st.wide.includes(id);
        w.classList.toggle("collapsed", fold);
        w.classList.toggle("wide", twoCap && wide);
        const b = w.querySelector('[data-act="fold"]');
        b.textContent = fold ? "▸" : "▾";
        b.title = fold ? "aufklappen" : "einklappen";
        const wb = w.querySelector('[data-act="wide"]');
        if (wb) { wb.title = wide ? "halb breit" : "ganz breit"; wb.setAttribute("aria-label", wb.title); }
      });
      box.dispatchEvent(new CustomEvent("cards:layout", { bubbles: true }));
    }
    let timer = null;
    function save(st) {
      render(st);
      saved = { ...snapshot(st, twoCap), at: Date.now() };
      try { localStorage.setItem(opts.key, JSON.stringify(saved)); } catch { /* privat */ }
      clearTimeout(timer);
      timer = setTimeout(() => {
        const csrf = (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
        fetch("/api/me/prefs", { method: "PUT", headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf },
          body: JSON.stringify({ [opts.part]: saved }) }).catch(() => {});
      }, 500);
    }

    box.addEventListener("click", (e) => {
      const b = e.target.closest(".kt-dock button");
      if (b) {
        const id = b.closest(".kt-item").dataset.id, a = b.dataset.act;
        if (a === "up" || a === "down") {
          const dir = a === "up" ? -1 : 1;
          save(two() ? step(cur, id, dir) : { ...cur, order: move(cur.order, id, dir) });
        } else if (a === "fold") save(toggle(cur, "collapsed", id));
        else if (a === "wide" || a === "col") save(toggle(cur, a, id));
        return;
      }
      // eingeklappte Kachel: Klick auf den Titel klappt auf
      const w = e.target.closest(".kt-item.collapsed");
      if (w) save(toggle(cur, "collapsed", w.dataset.id));
    });

    // Ziehen (Maus): ⠿ anfassen -> Rahmen wird ziehbar, beim Ziehen live einsortieren
    let dragging = null;
    box.addEventListener("pointerdown", (e) => {
      const g = e.target.closest(".kt-dock .grab");
      if (g) g.closest(".kt-item").draggable = true;
    });
    box.addEventListener("dragstart", (e) => {
      dragging = e.target.closest(".kt-item"); if (!dragging) return;
      dragging.classList.add("dragging"); box.classList.add("dragmode"); e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", dragging.dataset.id); } catch { /* egal */ }
    });
    function target(e) {
      const id = dragging.dataset.id;
      const over = e.target.closest(".kt-item");
      if (over && over !== dragging && box.contains(over)) {
        const r = over.getBoundingClientRect();
        const col = two() && !cur.wide.includes(over.dataset.id) ? cur.col[over.dataset.id] : null;
        return place(cur, id, over.dataset.id, e.clientY > r.top + r.height / 2, col);
      }
      const c = two() && e.target.closest(".kt-col");
      if (c && !over) {   // freie Flaeche einer Spalte (auch leere): ans Ende dieser Zeile, in diese Spalte
        const ids = [...c.parentNode.querySelectorAll(":scope > .kt-col > .kt-item")].map((w) => w.dataset.id)
          .filter((x) => x !== id).sort((a, b) => cur.order.indexOf(a) - cur.order.indexOf(b));
        return place(cur, id, ids[ids.length - 1] || null, true, c.dataset.col);
      }
      if (!over) {   // flacher Bereich: nach Hoehe einsortieren
        const after = [...box.querySelectorAll(".kt-item")].filter((w) => w !== dragging)
          .find((w) => { const r = w.getBoundingClientRect(); return e.clientY < r.top + r.height / 2; });
        return after ? place(cur, id, after.dataset.id, false, null) : place(cur, id, null, true, null);
      }
      return null;
    }
    box.addEventListener("dragover", (e) => {
      if (!dragging) return;
      e.preventDefault();
      const st = target(e);
      if (st && JSON.stringify(snapshot(st)) !== JSON.stringify(snapshot(cur))) render(st);
    });
    box.addEventListener("drop", (e) => e.preventDefault());
    box.addEventListener("dragend", () => {
      if (!dragging) return;
      dragging.classList.remove("dragging"); box.classList.remove("dragmode"); dragging.draggable = false; dragging = null;
      save(cur);
    });

    const reset = opts.reset && document.getElementById(opts.reset);
    if (reset) reset.addEventListener("click", () => save(norm(null, defs)));
    if (mq) {
      const re = () => render(cur);
      if (mq.addEventListener) mq.addEventListener("change", re); else if (mq.addListener) mq.addListener(re);
    }

    render(cur);
    // Stand vom Login holen – juenger als der im Browser? dann gilt er (anderes Geraet)
    fetch("/api/me/prefs").then((r) => (r.ok ? r.json() : {})).then((p) => {
      const best = newer(p && p[opts.part], saved && saved.at ? saved : null);
      if (best && best !== saved) {
        saved = best;
        render(norm(best, defs));
        try { localStorage.setItem(opts.key, JSON.stringify(best)); } catch { /* privat */ }
      }
    }).catch(() => {});
    return { render, get: () => cur };
  }
  root.Cards = { ...api, mount };
})(typeof window !== "undefined" ? window : globalThis);
