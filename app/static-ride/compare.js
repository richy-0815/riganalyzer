/* Routenvergleich: reine Helfer, im Browser window.CompareUI, in Node per require. */
(function (root) {
  /** Welche Route ist aktiv, nachdem `removed` aus `len` Routen herausgenommen wurde? */
  function nextActive(active, removed, len) {
    if (active > removed) return active - 1;
    if (active === removed) return Math.min(removed, len - 2);
    return active;
  }

  /** Lesbarer Spaltenname: .gpx und Unterstriche weg, ganz lang erst ab 48 Zeichen gekuerzt (Rest umbricht). */
  function colName(s, i) {
    const n = (s || "").replace(/\.gpx$/i, "").replace(/_/g, " ").trim();
    if (!n) return `Route ${i + 1}`;
    return n.length > 48 ? n.slice(0, 47) + "…" : n;
  }

  /** Feste Farbe je Tour im Vergleich – Blau/Violett/Pink/Petrol/Braun/Schiefer, bewusst keine Ampelfarben,
      damit sie nicht mit der Bewertungsskala der aktiven Route verwechselt werden. */
  const COLORS = ["#2f6fdf", "#9b4dca", "#d63d8f", "#0f9e9e", "#8a5a2b", "#5c6b7a"];
  const colorOf = (i) => COLORS[((i % COLORS.length) + COLORS.length) % COLORS.length];

  const api = { nextActive, colName, COLORS, colorOf };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CompareUI = api;
})(typeof window !== "undefined" ? window : globalThis);
