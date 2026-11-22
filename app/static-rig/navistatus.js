/* Navi-Paket-Status: Balken + Schrittliste + Laufzeit statt nur „40 %“.
   Gleiche Anzeige im Planer (#pl-navi-st) und im Konto („Meine Touren“). Die Schritte liefert der Server mit
   (package.STEPS → status.steps), damit Seite und Paketbau nicht auseinanderlaufen.
   Reine Funktionen (view, dur, eta) auch fuer node --test; im Browser window.NaviStatus. */
(function (root) {
  const FALLBACK_STEPS = [
    { doing: "Route wird berechnet", done: "Route berechnet" },
    { doing: "Straßendaten werden ausgeschnitten", done: "Straßendaten ausgeschnitten" },
    { doing: "Karte wird geladen", done: "Karte geladen" },
    { doing: "Gespann-Prüfung läuft", done: "Gespann geprüft" },
    { doing: "Paket wird abgeschlossen", done: "Paket abgeschlossen" },
  ];
  const de = (x, d = 1) => x.toLocaleString("de-DE", { maximumFractionDigits: d });

  /** Sekunden → „45 s“, „1:20 Min.“, „1 Std. 05 Min.“ */
  function dur(sec) {
    const s = Math.max(0, Math.round(sec || 0));
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} Min.`;
    return `${Math.floor(s / 3600)} Std. ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} Min.`;
  }

  /** Restzeit in Sekunden → Text. Unter einer Minute in Sekunden (5er-Schritte), kurz vor Schluss „gleich fertig“. */
  function rest(sec) {
    if (sec == null || !Number.isFinite(sec)) return null;
    if (sec <= 10) return "gleich fertig";
    if (sec < 60) return `noch ca. ${Math.ceil(sec / 5) * 5} s`;
    const min = Math.ceil(sec / 60);
    return min >= 90 ? `noch ca. ${de(min / 60)} Std.` : `noch ca. ${min} Min.`;
  }

  /** Notbehelf ohne Server-Schätzung (noch kein fertiger Bau gemerkt): aus Laufzeit und Prozent – erst ab 20 % und 30 s. */
  function eta(elapsed, pct) {
    if (!(pct >= 20 && pct < 100 && elapsed >= 30)) return null;
    return rest((elapsed * (100 - pct)) / pct);
  }

  /** Status vom Server → was angezeigt wird. elapsed: Sekunden seit Baubeginn (vom Server, lokal weitergezählt);
      since: Sekunden seit der letzten Antwort (zieht die Server-Restzeit p.eta herunter). */
  function view(p, elapsed, since = 0) {
    const state = (p && p.state) || "none";
    const list = (p && Array.isArray(p.steps) && p.steps.length ? p.steps : FALLBACK_STEPS);
    const at = p && Number.isInteger(p.step) ? p.step : -1;
    const v = { state, pct: 0, title: "", sub: "", steps: [], time: "", eta: null };
    if (state === "none") { v.title = "kein Navi-Paket"; return v; }
    if (state === "ready") {
      v.pct = 100;
      v.title = `📱 Navi-Paket bereit · ${de((p.size || 0) / 1e9)} GB`;
      v.sub = "In RigNavi unter „Meine Reisen“ laden.";
      if (p.took) v.time = `gebaut in ${dur(p.took)}`;
      return v;
    }
    if (state === "queued") {
      v.title = "📱 Navi-Paket wartet auf den Bau";
      const n = p.ahead || 0;
      v.sub = n > 0 ? `${n === 1 ? "Ein Paket ist" : `${n} Pakete sind`} vor dir dran.` : "Gleich geht's los.";
      v.steps = list.map((s) => ({ label: s.doing, state: "todo" }));
      if (p.waiting != null) v.time = `wartet seit ${dur(p.waiting)}`;
      return v;
    }
    if (state === "error") {
      v.title = "⚠ Navi-Paket fehlgeschlagen";
      v.sub = p.msg || "unbekannter Fehler";
      v.steps = at >= 0 ? list.map((s, i) => ({ label: i < at ? s.done : s.doing, state: i < at ? "done" : i === at ? "fail" : "todo" })) : [];
      return v;
    }
    v.pct = Math.min(99, Math.max(0, p.progress || 0));
    v.title = "📱 Navi-Paket wird gebaut";
    v.steps = list.map((s, i) => ({ label: i < at ? s.done : s.doing, state: i < at ? "done" : i === at ? "now" : "todo" }));
    if (elapsed != null) {
      v.time = `läuft seit ${dur(elapsed)}`;
      v.eta = p.eta != null ? rest(p.eta - since) : eta(elapsed, v.pct);
    }
    return v;
  }

  const api = { view, dur, eta, rest, FALLBACK_STEPS };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  /* ---------------------------------------------------------------- DOM */
  const el = (tag, props = {}, ...kids) => {
    const n = Object.assign(document.createElement(tag), props);
    kids.forEach((k) => k != null && n.append(k));
    return n;
  };
  const MARK = { done: "✓", now: "", todo: "", fail: "✕" };

  /** Status in node zeichnen. opts.onRetry: Knopf „Neu bauen“ im Fehlerfall; opts.compact: nur Kopf + Balken. */
  function render(node, p, opts = {}) {
    const elapsed = p && p.state === "building" ? (p.elapsed != null ? p.elapsed : null) : null;
    node._ns = { p, opts, at: Date.now() / 1000, t0: elapsed != null ? Date.now() / 1000 - elapsed : null };
    paint(node);
  }

  function paint(node) {
    const { p, opts, t0, at } = node._ns;
    const now = Date.now() / 1000;
    const v = view(p, t0 != null ? now - t0 : null, now - at);
    node.classList.add("ns");
    node.dataset.state = v.state;
    if (v.state === "none") { node.replaceChildren(v.title); return; }
    const head = el("div", { className: "ns-head" }, el("b", { className: "ns-title", textContent: v.title }),
      v.state === "building" ? el("span", { className: "ns-pct", textContent: `${v.pct} %` }) : null,
      v.time ? el("span", { className: "ns-time", textContent: v.time + (v.eta ? ` · ${v.eta}` : "") }) : null);
    const kids = [head];
    if (v.state === "building" || v.state === "queued") {
      const bar = el("div", { className: "ns-bar" }, el("span", { className: "ns-fill" }));
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-valuemin", "0"); bar.setAttribute("aria-valuemax", "100");
      bar.setAttribute("aria-valuenow", String(v.pct));
      bar.firstChild.style.width = `${v.state === "queued" ? 0 : v.pct}%`;
      kids.push(bar);
    }
    if (v.sub) kids.push(el("div", { className: "ns-sub", textContent: v.sub }));
    if (v.steps.length && !opts.compact) {
      kids.push(el("ol", { className: "ns-steps" }, ...v.steps.map((s) =>
        el("li", { className: `ns-step ${s.state}` }, el("span", { className: "ns-mark", textContent: MARK[s.state] }),
          s.label + (s.state === "now" ? " …" : "")))));
    }
    if (v.state === "error" && opts.onRetry) {
      const b = el("button", { type: "button", className: "ghost small ns-retry", textContent: "↻ Neu bauen" });
      b.addEventListener("click", () => { b.disabled = true; opts.onRetry(); });
      kids.push(b);
    }
    node.replaceChildren(...kids);
  }

  // Laufzeit sekündlich weiterzählen (ohne den Server zu fragen)
  setInterval(() => {
    document.querySelectorAll(".ns[data-state='building']").forEach((n) => { if (n._ns) paint(n); });
  }, 1000);

  root.NaviStatus = { ...api, render };
})(typeof window !== "undefined" ? window : globalThis);
