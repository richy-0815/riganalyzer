/* Kleine Text-Helfer fuer den Routenplaner (planner.js, Ride + Rig) – im Browser window.PlanText,
   in Node per require (tests/js/plantext.test.cjs). */
(function (root) {
  // Fahrzeit wie in der Planer-Anzeige: 654 -> "10:54 h"
  const hm = (min) => `${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, "0")} h`;

  // Zeitunterschied der Varianten: unter 60 Minuten „+25 min“, ab 60 wie die Fahrzeit „+10:54 h“
  function signedTime(dm) {
    const n = Math.round(dm);
    const sign = n > 0 ? "+" : n < 0 ? "−" : "±";
    const a = Math.abs(n);
    return a >= 60 ? `${sign}${hm(a)}` : `${sign}${a} min`;
  }

  // Punkt-Namen. custom = selbst vergeben (bleibt beim Verschieben, wird nie durch Vorschlaege ersetzt),
  // auto = Vorschlag aus /api/place (wird nach dem Verschieben neu ermittelt).
  const NAME_MAX = 60;
  const coords = (p) => `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`;
  const label = (p) => p.name || coords(p);
  function rename(p, text) {
    const v = String(text == null ? "" : text).replace(/\s+/g, " ").trim().slice(0, NAME_MAX);
    delete p.auto;
    if (v) { p.name = v; p.custom = true; } else { p.name = ""; delete p.custom; }
    return p;
  }
  // nach dem Verschieben: eigener Name bleibt, alles andere (Vorschlag, Suchtreffer) passt nicht mehr zum Ort
  function afterMove(p) {
    if (!p.custom) { p.name = ""; delete p.auto; }
    return p;
  }
  // Vorschlag nur in einen noch namenlosen, nicht selbst benannten Punkt uebernehmen
  function applySuggestion(p, name) {
    if (!name || p.custom || p.name) return false;
    p.name = String(name).slice(0, NAME_MAX); p.auto = true;
    return true;
  }
  // Name fuer Navi/Tour-Stopps: Suchtreffer nur bis zum ersten Komma („Wetzlar, Hessen, …“), eigene Namen ganz
  const stopName = (p) => (!p.name ? "" : p.custom ? p.name : p.name.split(",")[0]);

  const api = { hm, signedTime, NAME_MAX, coords, label, rename, afterMove, applySuggestion, stopName };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PlanText = api;
})(typeof window !== "undefined" ? window : globalThis);
