/* Mein Konto – Kacheln verschieben und einklappen (Ride + Rig), wie das Dashboard der Hauptseite:
   ⠿ ziehen, ↑/↓, ▾ einklappen. Anordnung im Browser und am Login (prefs „konto“, der neuere Stand gewinnt).
   Die Logik steckt in cards.js (gemeinsam mit dem Admin-Dashboard); hier nur eine Spalte einrichten. */
(function (root) {
  if (typeof module !== "undefined" && module.exports) { module.exports = require("./cards.js"); return; }
  root.KontoCards = root.Cards;
  root.Cards.mount({ box: "kt-cards", attr: "kcard", key: "konto.layout", part: "konto", reset: "kt-reset" });
})(typeof window !== "undefined" ? window : globalThis);
