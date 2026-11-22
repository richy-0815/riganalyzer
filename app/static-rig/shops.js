/* „🛒 Einkaufen“ (RigAnalyzer): Discounter, Supermaerkte, Baecker, Metzger, Hoflaeden entlang der Route –
   Filter-Chips, Liste nach km, Kartenebene, Popup mit Oeffnungszeiten. Nur Rig. Laeuft nach rig.js und camps-filter.js.
   Reine Funktionen auch fuer Node (Tests); OSM-Texte (Name, Marke, Adresse, Zeiten) landen nie per innerHTML im DOM. */
(function (root) {
  const KINDS = ["discount", "supermarket", "bakery", "butcher", "farm"];
  const LABEL = { discount: "Discounter", supermarket: "Supermarkt", bakery: "Bäcker", butcher: "Metzger", farm: "Hofladen" };
  const ICON = { discount: "🛒", supermarket: "🏪", bakery: "🥖", butcher: "🥩", farm: "🧺" };
  const shopKindLabel = (k) => LABEL[k] || "Laden";
  const shopIcon = (k) => ICON[k] || "🛒";

  /** f: {kinds: [..] (leer/fehlt = alle), openNow}; „Zeiten unbekannt“ bleibt bei openNow drin */
  function filterShops(pois, f, now) {
    const kinds = (f && f.kinds) || [];
    return (pois || []).filter((p) => {
      if (p.group !== "shop") return false;
      const x = p.flags || {};
      if (kinds.length && !kinds.includes(x.kind)) return false;
      if (f && f.openNow && root.CampFilter.isOpen(x.hours, null, now) === false) return false;
      return true;
    });
  }

  /** „offen bis HH:MM“ / „öffnet HH:MM“ / „geschlossen“ / „Zeiten prüfen“ (unlesbar oder leer) */
  function nextOpenText(hours, now) {
    const open = root.CampFilter.isOpen(hours, null, now);
    if (open === null) return "Zeiten prüfen";
    const c = root.CampFilter.nextChange(hours, now);
    if (open) return c && c.open ? `offen bis ${c.at}` : "offen";
    return c && !c.open ? `öffnet ${c.at}` : "geschlossen";
  }

  /** Text fuer die Etappen-Karte aus st.last_shop; null ohne Laden */
  function stageHint(ls) {   // bewusst ohne offen/zu: die Etappe liegt in der Zukunft
    if (!ls) return null;
    return `🛒 Letzter Einkauf vor dem Ziel: ${ls.name} (${ls.label}), ${Number(ls.km_before).toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} km vorher – ${ls.hours || "Zeiten unbekannt"}`;
  }

  // Mehrere Etappen wiederholen denselben Hinweis (kein DE-Bezug / Daten fehlen): in der Reise nur der erste bleibt
  function dedupeHints(notes) {
    const seen = new Set();
    return notes.filter((n) => {
      if (n.t !== "shop") return true;
      if (seen.has(n.label)) return false;
      seen.add(n.label);
      return true;
    });
  }

  const shopName0 = (p) => p.name || p.label || (p.flags && p.flags.brand) || "Laden";
  /* „➕ In die Tour“ fuer alle Orte entlang der Route: Laden, Platz/Ver-/Entsorgung, Tankstelle, Rastplatz.
     what: shop | camp | fuel | rest – bestimmt nur den Namens-Ersatz und landet in poi.what. */
  const FALLBACK = { shop: "Laden", camp: "Platz", fuel: "Tankstelle", rest: "Rastplatz",
    sight: "Sehenswertes", food: "Einkehr", service: "Service" };   // Reiter „Unterwegs“
  const poiName = (p, what = "shop") => (what === "shop" ? shopName0(p) : p.name || p.label || FALLBACK[what] || "Zwischenpunkt");
  /** Ort -> Planer-Zwischenpunkt (kein Nacht-Stopp) */
  const toWaypoint = (p, what = "shop") => ({ lat: p.lat, lon: p.lon, name: poiName(p, what), stop: false,
    poi: { osm: p.osm || null, what, kind: (p.flags || {}).kind || null } });
  /** schon in der Tour? gleiche OSM-Id oder gleiche Koordinate */
  const isInTour = (p, pts) => (pts || []).some((q) => (p.osm && q.poi && q.poi.osm === p.osm) || (q.lat === p.lat && q.lon === p.lon));
  const tourLabel = (inTour) => (inTour ? "✓ in der Tour" : "➕ In die Tour");

  const api = { toWaypoint, isInTour, poiName, tourLabel, dedupeHints, stageHint, filterShops, shopKindLabel, shopIcon, nextOpenText, KINDS };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  /* ---- Browser: ab hier DOM/Leaflet; nutzt rig.js (route, tab, layers, map, esc-freie DOM-Bauweise, fmt, $) ---- */
  const KEY = "rig.shopfilter";
  const F = (() => { try { return JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { return {}; } })();
  if (!Array.isArray(F.kinds)) F.kinds = [];
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(F)); } catch { /* nur Komfort */ } };
  const markers = new Map();   // osm-id -> Marker (Liste oeffnet das Popup)
  const el = (tag, props, ...kids) => {
    const n = document.createElement(tag);
    Object.assign(n, props || {});
    for (const k of kids) if (k != null && k !== false) n.append(k);
    return n;
  };
  const shopName = shopName0;
  const planner = () => (typeof window !== "undefined" && window.planner && window.planner.addWaypoint && window.plannerResult ? window.planner : null);
  /* Knopf „➕ In die Tour“ bzw. „✓ in der Tour“; ohne Planer-Route (GPX-Import) keiner. Alle Knoepfe tragen den Ort in
     data-Attributen, ein gemeinsamer Klick-Handler (unten) uebernimmt – so geht es auch in innerHTML-Listen/Popups. */
  const chipData = (p, what) => ({ ll: `${p.lat},${p.lon}`, osm: p.osm || "", name: poiName(p, what), what, kind: (p.flags || {}).kind || "" });
  function tourButton(p, what = "shop") {
    const pl = planner();
    if (!pl) return null;
    const inTour = pl.hasPoint(p);
    const b = el("button", { type: "button", className: "ghost small shoptour", disabled: inTour, textContent: tourLabel(inTour) });
    Object.assign(b.dataset, Object.fromEntries(Object.entries(chipData(p, what)).map(([k, v]) => [`tour${k[0].toUpperCase()}${k.slice(1)}`, v])));
    return b;
  }
  /** dasselbe als HTML-Text fuer Listen/Popups, die per innerHTML gebaut werden (camps.js, rig.js); "" ohne Planer */
  function tourChipHtml(p, what) {
    const b = tourButton(p, what);
    return b ? b.outerHTML : "";
  }
  function refreshTourChips() {
    const pl = planner();
    document.querySelectorAll(".shoptour[data-tour-ll]").forEach((b) => {
      const [lat, lon] = b.dataset.tourLl.split(",").map(Number);
      const inTour = !!pl && pl.hasPoint({ lat, lon, osm: b.dataset.tourOsm || null });
      b.disabled = inTour; b.textContent = tourLabel(inTour);
    });
  }
  if (typeof document !== "undefined" && document.addEventListener) document.addEventListener("click", (e) => {
    const b = e.target.closest(".shoptour[data-tour-ll]");
    if (!b) return;
    e.stopPropagation();   // capture: Zeile nicht auf die Karte zentrieren, kein Popup-Handler
    const pl = planner();
    if (!pl || b.disabled) return;
    const d = b.dataset, [lat, lon] = d.tourLl.split(",").map(Number);
    const w = { lat, lon, name: d.tourName, stop: false, poi: { osm: d.tourOsm || null, what: d.tourWhat, kind: d.tourKind || null } };
    if (pl.addWaypoint(w)) refreshTourChips();
    else { b.disabled = true; b.textContent = "Tour ist voll (25 Punkte)"; }
  }, true);
  const detail = (p) => {
    const x = p.flags || {};
    return [shopKindLabel(x.kind), x.brand && x.brand !== p.name ? x.brand : null].filter(Boolean).join(" · ");
  };

  function shopPopup(p) {
    const x = p.flags || {};
    return root.DetailCard.card(p, {
      icon: shopIcon(x.kind), title: shopName(p), group: "shop",
      chips: [shopKindLabel(x.kind), x.brand && x.brand !== p.name ? x.brand : null].filter(Boolean),
      hours: x.hours || "", phone: x.phone, website: x.website, email: x.email, desc: x.description,
      actions: [tourButton(p)], extra: x.addr ? [el("p", { className: "dc-addr", textContent: `📍 ${x.addr}` })] : [],
    });
  }

  function drawShopLayer(r, layer) {
    layer.clearLayers();
    markers.clear();
    for (const p of filterShops(r.pois || [], F, new Date())) {
      const m = L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "", html: `<div class="pin shop">${shopIcon((p.flags || {}).kind)}</div>`, iconSize: [20, 20], iconAnchor: [10, 10] }) })
        .bindPopup(() => shopPopup(p)).addTo(layer);
      if (p.osm) markers.set(p.osm, m);
    }
  }

  function renderShopFilters(box, onChange) {
    box.hidden = tab !== "shop";
    if (box.hidden) return;
    box.replaceChildren();
    const chip = (on, text, fn) => {
      const b = el("button", { type: "button", className: "chipf", textContent: text });
      b.setAttribute("aria-pressed", String(!!on));
      b.addEventListener("click", () => { fn(); save(); onChange(); });
      return b;
    };
    const kinds = el("div", { className: "cfg" }, el("span", { textContent: "Art" }),
      ...KINDS.map((k) => chip(F.kinds.includes(k), `${ICON[k]} ${LABEL[k]}`, () => {
        F.kinds = F.kinds.includes(k) ? F.kinds.filter((x) => x !== k) : [...F.kinds, k];
      })));
    const when = el("div", { className: "cfg" }, el("span", { textContent: "Zeiten" }),
      chip(F.openNow, "🕒 jetzt geöffnet", () => { F.openNow = !F.openNow; }));
    box.append(kinds, when);
  }

  function renderShopList(r, list) {
    list.replaceChildren();
    const all = (r.pois || []).filter((p) => p.group === "shop");
    if (!all.length) {
      const note = (r.notes || []).find((n) => n.t === "shop");
      list.append(el("li", { className: "empty", textContent: note ? note.text : "Keine Läden entlang der Route." }));
      return;
    }
    const shown = filterShops(all, F, new Date()).sort((a, b) => a.km - b.km);
    if (!shown.length) {
      list.append(el("li", { className: "empty", textContent: "Kein Laden passt zu den Filtern – ein paar abwählen." }));
      return;
    }
    list.append(el("li", { className: "empty", textContent: `${shown.length} von ${all.length} Läden` }));
    for (const p of shown) {
      const x = p.flags || {}, li = el("li", { className: "shoprow" });
      li.dataset.ll = `${p.lat},${p.lon}`;
      li.dataset.shop = p.osm || "";
      li.append(el("span", { className: "km", textContent: `km ${fmt(p.km, 1)}` }),
        el("span", {}, `${shopIcon(x.kind)} ${shopName(p)} `,
          el("small", { textContent: `${detail(p)} · ${fmt(p.off)} m abseits · ${nextOpenText(x.hours, new Date())}` })));
      const tb = tourButton(p);
      if (tb) li.append(tb);
      list.append(li);
    }
  }

  root.ShopList = { ...api, drawShopLayer, renderShopFilters, renderShopList, shopPopup, stageHint, tourChipHtml, tourButton, refreshTourChips };

  // Popups werden vorab als Text gebaut – beim Oeffnen den Stand der „In die Tour“-Knoepfe auffrischen
  if (typeof map !== "undefined" && map.on) map.on("popupopen", refreshTourChips);
  // Klick in der Liste: rig.js zoomt hin, hier zusaetzlich das Popup oeffnen (Ebene ggf. einschalten)
  const list = document.getElementById("list");
  if (list) list.addEventListener("click", (e) => {
    const li = e.target.closest("[data-shop]");
    const m = li && markers.get(li.dataset.shop);
    if (!m) return;
    const cb = document.querySelector('#toggles input[data-m="shop"]');
    if (cb && !cb.checked) { cb.checked = true; syncToggles(); }
    setTimeout(() => m.openPopup(), 50);
  });
  const box = document.getElementById("shop-filters");
  if (box) root.shopsChanged = () => {
    if (!route) return;
    renderShopFilters(box, root.shopsChanged); renderShopList(route, document.getElementById("list")); drawShopLayer(route, layers.shop); syncToggles();
  };
})(typeof window !== "undefined" ? window : globalThis);
