/* Tempo je Land als Satz fuer „Maut, Vignette & Tempo“ (RigAnalyzer) – aus speedrules.json,
   derselben Tabelle wie in RigNavi. Im Browser window.SpeedText, in Node per require. */
(function (root) {
  function speedLine(rules, cc, veh) {
    const c = rules && rules.countries && rules.countries[cc];
    if (!c) return "";
    const cls = veh.trailer ? (cc === "DE" && veh.tempo100 ? "trailer100" : "trailer") : veh.zgg > 3.5 ? "heavy" : "car";
    if (cls === "car") return "Bis 3,5 t ohne Anhänger: wie Pkw.";
    const l = c.limits[cls];
    const parts = [["Autobahn", l.motorway], ["Schnellstraße", l.expressway], ["außerorts", l.rural]]
      .filter(([, v]) => v).map(([t, v]) => `${t} ${v}`);
    const who = cls === "heavy" ? "Über 3,5 t" : cls === "trailer100" ? "Gespann mit Tempo-100-Zulassung" : "Gespann";
    return parts.length ? `${who}: ${parts.join(", ")} km/h (innerorts laut Schild).` : "";
  }

  const api = { speedLine };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SpeedText = api;
})(typeof window !== "undefined" ? window : globalThis);
