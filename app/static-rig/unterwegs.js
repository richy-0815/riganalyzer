/* „🧭 Unterwegs entdecken“ (nur RigAnalyzer): Sehenswertes, Einkehr und Service entlang der Route –
   Filter-Chips, Liste nach km, Kartenebene, Popup, „➕ In die Tour“ (aus shops.js). Läuft nach shops.js.
   Quellen: Sehenswertes/Einkehr aus der Analyse (lokale Kartendaten, bis 800 m bzw. 250 m neben der Route), Service aus
   dem Einkaufs-Datensatz (Werkstätten, Caravan, Gasflaschen, Waschsalon – bisher nur Deutschland, bis 2 km).
   Reine Funktionen auch für Node; OSM-Texte landen nie per innerHTML im DOM. */
(function (root) {
  const CATS = ["sight", "food", "service"];
  const CAT_LABEL = { sight: "Sehenswertes", food: "Einkehr", service: "Service" };
  const CAT_ICON = { sight: "⭐", food: "🍽", service: "🔧" };
  const SIGHT_ICON = { Aussichtspunkt: "🔭", Aussichtsturm: "🔭", Museum: "🏛", "Burg/Schloss": "🏰", Festung: "🏰", Ruine: "🏚",
    Kloster: "⛪", Wasserfall: "💧", Pass: "⛰", Gipfel: "🏔", Badestelle: "🏖", Tierpark: "🦌", Leuchtturm: "🗼" };
  const FOOD_ICON = { Café: "☕", Restaurant: "🍽", Biergarten: "🍺", Eiscafé: "🍦" };
  const SERVICE_ICON = { repair: "🔧", truck_repair: "🚛", caravan: "🚐", gas: "🔥", laundry: "🧺" };

  function icon(p) {
    if (p.group === "sight") return SIGHT_ICON[p.label] || CAT_ICON.sight;
    if (p.group === "food") return FOOD_ICON[p.label] || CAT_ICON.food;
    return SERVICE_ICON[(p.flags || {}).kind] || CAT_ICON.service;
  }
  const hasHours = (p) => p.group !== "sight";

  /** f: {cats: [..] (leer/fehlt = alle), openNow}; Sehenswertes hat keine Zeiten und bleibt bei openNow drin,
      „Zeiten unbekannt“ ebenso (wie bei den Läden) */
  function filterWay(pois, f, now) {
    const cats = (f && f.cats) || [];
    return (pois || []).filter((p) => {
      if (!CATS.includes(p.group)) return false;
      if (cats.length && !cats.includes(p.group)) return false;
      if (f && f.openNow && hasHours(p) && root.CampFilter.isOpen((p.flags || {}).hours, null, now) === false) return false;
      return true;
    });
  }
  /** Sehenswertes zuerst nach Bedeutung sortieren? Nein – unterwegs zählt die Reihenfolge der Strecke: nach km */
  const byKm = (a, b) => a.km - b.km;
  const name = (p) => p.name || p.label || CAT_LABEL[p.group] || "Ort";
  const counts = (pois) => Object.fromEntries(CATS.map((c) => [c, (pois || []).filter((p) => p.group === c).length]));

  const api = { CATS, CAT_LABEL, filterWay, icon, name, counts, byKm };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  /* ---- Browser: nutzt rig.js (route, tab, layers, map, fmt, syncToggles) und shops.js (ShopList.tourButton, nextOpenText) ---- */
  const KEY = "rig.wayfilter";
  const F = (() => { try { return JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { return {}; } })();
  if (!Array.isArray(F.cats)) F.cats = [];
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(F)); } catch { /* nur Komfort */ } };
  const markers = new Map();
  const el = (tag, props, ...kids) => {
    const n = document.createElement(tag);
    Object.assign(n, props || {});
    for (const k of kids) if (k != null && k !== false) n.append(k);
    return n;
  };
  const detail = (p) => [p.label, p.group === "sight" && p.wiki ? "Wikipedia" : null].filter(Boolean).join(" · ");
  const hoursText = (p) => (hasHours(p) ? root.ShopList.nextOpenText((p.flags || {}).hours, new Date()) : null);

  /** Wikipedia-Link aus „de:Titel“ */
  const wikiHref = (w) => {
    const m = /^([a-z]{2,3}):(.+)$/.exec(String(w || ""));
    return m ? `https://${m[1]}.wikipedia.org/wiki/${encodeURIComponent(m[2].replace(/ /g, "_"))}` : "";
  };
  // Popup als Detail-Karte (detailcard.js); Service mit Leistungen und Einfahrt-Foto
  function wayPopup(p) {
    const x = p.flags || {}, DC = root.DetailCard;
    const chips = p.group === "service" ? [DC.SERVICE_KIND[x.kind] || p.label, x.brand || x.operator].filter(Boolean) : [p.label].filter(Boolean);
    const more = [];
    const wk = wikiHref(x.wikipedia);
    if (wk) more.push({ href: wk, text: "Wikipedia ↗" });
    if (x.image) more.push({ href: x.image, text: "Foto (aus OSM) ↗" });
    const photos = [DC.commonsPhoto(x.commons)].filter(Boolean);
    return DC.card(p, {
      icon: icon(p), title: name(p), group: p.group, chips,
      hours: hasHours(p) ? x.hours || "" : x.hours, phone: x.phone, website: x.website, email: x.email, desc: x.description,
      photos, mly: p.group === "service", more, servicesTitle: p.group === "service" ? "Leistungen laut OSM" : "",
      note: p.group === "service" && x.operator && x.brand && x.operator !== x.brand ? `Betreiber: ${x.operator}` : "",
      actions: [root.ShopList.tourButton(p, p.group)],
      extra: x.addr ? [el("p", { className: "dc-addr", textContent: `📍 ${x.addr}` })] : [],
    });
  }

  function drawWayLayer(r, layer) {
    layer.clearLayers();
    markers.clear();
    for (const p of filterWay(r.pois || [], F, new Date())) {
      const m = L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "", html: `<div class="pin way">${icon(p)}</div>`, iconSize: [20, 20], iconAnchor: [10, 10] }) })
        .bindPopup(() => wayPopup(p)).addTo(layer);
      if (p.osm) markers.set(p.osm, m);
    }
  }

  function renderWayFilters(box, onChange) {
    box.hidden = tab !== "way";
    if (box.hidden) return;
    box.replaceChildren();
    const chip = (on, text, fn) => {
      const b = el("button", { type: "button", className: "chipf", textContent: text });
      b.setAttribute("aria-pressed", String(!!on));
      b.addEventListener("click", () => { fn(); save(); onChange(); });
      return b;
    };
    const n = counts(route && route.pois);
    box.append(el("div", { className: "cfg" }, el("span", { textContent: "Was" }),
      ...CATS.map((c) => chip(F.cats.includes(c), `${CAT_ICON[c]} ${CAT_LABEL[c]} (${n[c]})`, () => {
        F.cats = F.cats.includes(c) ? F.cats.filter((x) => x !== c) : [...F.cats, c];
      }))),
    el("div", { className: "cfg" }, el("span", { textContent: "Zeiten" }),
      chip(F.openNow, "🕒 jetzt geöffnet", () => { F.openNow = !F.openNow; })));
  }

  function renderWayList(r, list) {
    list.replaceChildren();
    const all = (r.pois || []).filter((p) => CATS.includes(p.group));
    const svcNote = (r.notes || []).find((n) => n.t === "shop");   // kein DE-Bezug / Daten fehlen: gilt auch für Service
    if (!all.length) {
      list.append(el("li", { className: "empty", textContent: `Nichts Sehenswertes, keine Einkehr und kein Service direkt an der Route.${svcNote ? ` (${svcNote.text})` : ""}` }));
      return;
    }
    const shown = filterWay(all, F, new Date()).sort(byKm);
    if (!shown.length) {
      list.append(el("li", { className: "empty", textContent: "Nichts passt zu den Filtern – ein paar abwählen." }));
      return;
    }
    list.append(el("li", { className: "empty", textContent: `${shown.length} von ${all.length} Orten${svcNote ? ` · Service: ${svcNote.text}` : ""}` }));
    for (const p of shown) {
      const li = el("li", { className: "shoprow" });
      li.dataset.ll = `${p.lat},${p.lon}`;
      li.dataset.way = p.osm || "";
      const h = hoursText(p);
      li.append(el("span", { className: "km", textContent: `km ${fmt(p.km, 1)}` }),
        el("span", {}, `${icon(p)} ${name(p)} `, el("small", { textContent: [detail(p), `${fmt(p.off)} m abseits`, h].filter(Boolean).join(" · ") })));
      const tb = root.ShopList.tourButton(p, p.group);
      if (tb) li.append(tb);
      list.append(li);
    }
  }

  root.WayList = { ...api, drawWayLayer, renderWayFilters, renderWayList, wayPopup };

  const list = document.getElementById("list");
  if (list) list.addEventListener("click", (e) => {
    const li = e.target.closest("[data-way]");
    const m = li && markers.get(li.dataset.way);
    if (!m) return;
    const cb = document.querySelector('#toggles input[data-m="way"]');
    if (cb && !cb.checked) { cb.checked = true; syncToggles(); }
    setTimeout(() => m.openPopup(), 50);
  });
  const box = document.getElementById("way-filters");
  if (box) root.wayChanged = () => {
    if (!route) return;
    renderWayFilters(box, root.wayChanged); renderWayList(route, document.getElementById("list")); drawWayLayer(route, layers.way); syncToggles();
  };
})(typeof window !== "undefined" ? window : globalThis);
