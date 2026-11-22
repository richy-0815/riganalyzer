/* Suchfeld über den Listen (RigAnalyzer): filtert den offenen Reiter – Engstellen, Plätze & Versorgung,
   Tankstellen, Einkaufen, Rastplätze, Steigungen, Sprit-Info. Groß/klein und Umlaute egal, mehrere Wörter = alle
   müssen vorkommen. Knopf-Texte („➕ In die Tour“, „€ Preis holen“) zählen nicht mit. Reine Funktionen auch für Node. */
(function (root) {
  const norm = (s) => String(s || "").toLowerCase().replace(/ß/g, "ss").normalize("NFD").replace(/[̀-ͯ]/g, "");
  /** true, wenn alle Wörter aus q im Text stehen (leere Suche = alles) */
  function matches(text, q) {
    const words = norm(q).split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const t = norm(text);
    return words.every((w) => t.includes(w));
  }
  const countText = (shown, total) => (total ? `${shown} von ${total}` : "");
  const api = { norm, matches, countText };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.ListSearch = api;

  const input = document.getElementById("list-q");
  if (!input) return;
  const count = document.getElementById("list-q-n");
  const panes = ["list", "steep", "fuelinfo"].map((id) => document.getElementById(id)).filter(Boolean);
  const ROWS = ":scope > li, .issue, tbody tr";
  const rowText = (r) => {
    const c = r.cloneNode(true);
    c.querySelectorAll("button").forEach((b) => b.remove());
    return c.textContent;
  };
  function apply() {
    const q = input.value;
    let shown = 0, total = 0;
    for (const pane of panes) {
      let s = 0, n = 0;
      pane.querySelectorAll(ROWS).forEach((r) => {
        if (r.classList.contains("empty") || r.classList.contains("q-none")) return;
        const ok = matches(rowText(r), q);
        if (r.hidden !== !ok) r.hidden = !ok;
        n++; if (ok) s++;
      });
      // Hinweis, wenn die Suche alles ausblendet (nur einfügen/entfernen, wenn nötig – sonst feuert der Observer endlos)
      const none = pane.querySelector(":scope > .q-none");
      const want = n > 0 && s === 0 && q.trim() !== "";
      if (want && !none) pane.append(Object.assign(document.createElement(pane.tagName === "OL" ? "li" : "p"),
        { className: "empty q-none", textContent: "Nichts passt zur Suche." }));
      if (!want && none) none.remove();
      if (!pane.hidden) { shown += s; total += n; }
    }
    if (count) count.textContent = input.value.trim() ? countText(shown, total) : "";
  }
  input.addEventListener("input", apply);
  input.addEventListener("keydown", (e) => { if (e.key === "Escape" && input.value) { input.value = ""; apply(); } });
  // Listen werden bei jedem Reiterwechsel/Filter neu gebaut – dann Suche erneut anwenden
  const obs = new MutationObserver(apply);
  for (const p of panes) {
    obs.observe(p, { childList: true, subtree: true });
    new MutationObserver(apply).observe(p, { attributes: true, attributeFilter: ["hidden"] });
  }
})(typeof window !== "undefined" ? window : globalThis);
