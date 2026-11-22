/* Admin-Dashboard: Kacheln in zwei Spalten (ab 901 px), verschiebbar (⠿ ↑ ↓ ⇄), halb/ganz breit (⇔),
   einklappbar (▾) – Anordnung im Browser („admin.layout“) und am Login (prefs „admin“). Logik in cards.js. */
(function (root) {
  root.Cards.mount({ box: "adm-cards", attr: "acard", key: "admin.layout", part: "admin", reset: "adm-reset",
    two: "(min-width: 901px)" });
})(window);
