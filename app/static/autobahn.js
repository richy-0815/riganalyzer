/* Autobahn-Piktogramm und Umschalter im Routenplaner – ersetzt das 🛣-Emoji.
   Eigenes Inline-SVG (blaues Schild, zwei Richtungsfahrbahnen unter einer Bruecke), „durchgestrichen“ per CSS
   (.ab-pic.off bzw. [data-state="off"] > .ab-pic). Im Browser window.Autobahn, in Node per require. */
(function (root) {
  const SVG = '<svg viewBox="0 0 24 24" focusable="false">'
    + '<rect width="24" height="24" rx="5" fill="#1e5aa8"/>'
    + '<g fill="#fff">'
    + '<path d="M2.6 5.6h18.8v2.6H2.6z"/>'                      // Brueckenplatte
    + '<path d="M4.2 8.2h1.6v2.4H4.2zM18.2 8.2h1.6v2.4h-1.6z"/>' // Widerlager
    + '<path d="M3.4 21.4 10.3 21.4 11.2 9.8 10 9.8z"/>'          // linke Fahrbahn
    + '<path d="M13.7 21.4 20.6 21.4 14 9.8 12.8 9.8z"/>'         // rechte Fahrbahn
    + "</g>"
    + '<path d="M8.3 21.4 9.1 17.2M8.95 15.9 9.45 13.3M15.7 21.4 14.9 17.2M15.05 15.9 14.55 13.3" stroke="#1e5aa8" stroke-width=".9"/>'
    + "</svg>";

  const TEXT = { on: "mit", off: "ohne", mixed: "gemischt" };

  /** Piktogramm als Markup; off = durchgestrichen (Ride-Knopf „ohne“). */
  function pic(off) {
    return `<span class="ab-pic${off ? " off" : ""}" aria-hidden="true">${SVG}</span>`;
  }

  /** Zustand aller Abschnitte: "on" (alle mit), "off" (alle ohne), "mixed". Ohne Abschnitte zaehlt der Standard. */
  function state(modes, def) {
    const set = new Set(modes.length ? modes : [def]);
    if (set.size > 1) return "mixed";
    return set.has("noway") ? "off" : "on";
  }

  /** Ein Abschnitt: mit <-> ohne. */
  function next(mode) {
    return mode === "noway" ? "fast" : "noway";
  }

  /** Alle Abschnitte: gemischt -> mit, sonst umdrehen. */
  function nextAll(st) {
    return st === "on" ? "noway" : "fast";
  }

  /** Rig: ein Chip je Abschnitt; data-mode = Modus, auf den ein Klick schaltet (wie die frueheren Knoepfe). */
  function legChip(i, mode, label) {
    const st = mode === "noway" ? "off" : "on";
    return `<button type="button" class="ab-chip" role="switch" aria-checked="${st === "on"}" data-leg="${i}" `
      + `data-mode="${next(mode)}" data-state="${st}" aria-label="Autobahn ${TEXT[st]} – ${label}" `
      + `title="Autobahn auf diesem Abschnitt ${st === "on" ? "erlaubt – antippen zum Meiden" : "gemieden – antippen zum Erlauben"}">`
      + `${pic()}<span class="ab-txt">${TEXT[st]}</span></button>`;
  }

  /** Ride: Beschriftung der drei Abschnitts-Knoepfe. */
  function legLabel(mode) {
    if (mode === "fast") return pic();
    if (mode === "noway") return `${pic(true)} ohne`;
    return "kurvig";
  }

  const api = { pic, state, next, nextAll, legChip, legLabel, TEXT };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Autobahn = api;
})(typeof window !== "undefined" ? window : globalThis);
