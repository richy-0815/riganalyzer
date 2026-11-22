/* Detail-Karte: ein gemeinsamer, gegliederter Aufbau fuer die Popups von Stellplaetzen/Camping,
   Laeden, Werkstaetten/Service, Einkehr und Sehenswertem – statt Textzeile an Textzeile.

     Kopf        Symbol + Name, Art-Chips, eigene Bewertung, „km 123 · 300 m abseits“
     Knoepfe     Navigieren · Anrufen · Website · (In die Tour / Als Uebernachtung) · Mehr (OSM, park4night, E-Mail, Koordinaten)
     Fotos       Einfahrt (Mapillary, laedt beim Oeffnen), gemessenes Luftbild, Wikimedia-Foto ueber den eigenen Server
     Zeiten      „🟢 heute 08:30–12:30, 14:00–19:00“, darunter aufklappbar die Woche
     Beschreibung gekuerzt mit „mehr“
     Leistungen  Chips aus den OSM-Merkmalen (Strom, Wasser, Entsorgung … bzw. Wohnmobil-Service, Gas, Reifen …)
     Fuss        „Fehlt was? In OSM ergaenzen“

   Reine Funktionen auch fuer Node (Tests). Im Browser baut card() ein DOM-Element: alle Texte aus OSM landen ueber
   textContent im DOM, nie als HTML. Nur extra.html (von camps.js selbst escaped gebaut) wird als HTML eingesetzt. */
(function (root) {
  // ---------------------------------------------------------------- Leistungen (OSM-Merkmale -> Chips)
  // service:vehicle:* (Werkstaetten) – Schluessel so, wie sie in OSM wirklich vorkommen (gezaehlt im DE-Datensatz)
  const VEHICLE_SERVICE = {
    motor_home: "Wohnmobile", motorhome: "Wohnmobile", caravan_repair: "Wohnwagen-Reparatur", caravan: "Wohnwagen",
    caravan_sales: "Caravan-Verkauf", lpg: "Autogas", gas: "Gas", trailer: "Anhänger",
    car_repair: "Reparatur", repairs: "Reparatur", repair: "Reparatur", car_service: "Wartung", service: "Wartung",
    tyres: "Reifen", tires: "Reifen", tyres_repair: "Reifenreparatur", wheels: "Räder/Felgen", wheel_repair: "Felgen",
    inspection: "HU/AU", oil_change: "Ölwechsel", glass: "Scheiben", glass_repair: "Scheiben", windshields: "Scheiben",
    diagnostics: "Diagnose", brakes: "Bremsen", body_repair: "Karosserie", painting: "Lackierung", air_conditioning: "Klimaanlage",
    batteries: "Batterien", electrical: "Elektrik", car_parts: "Ersatzteile", parts: "Ersatzteile", truck_parts: "Lkw-Teile",
    motor: "Motor", engine: "Motor", alignment: "Spur", towing: "Abschleppdienst", breakdown: "Pannenhilfe",
    muffler: "Auspuff", exhausts: "Auspuff", exhaust: "Auspuff", transmission: "Getriebe", transmission_repair: "Getriebe",
    suspension: "Fahrwerk", truck_repair: "Lkw", truck: "Lkw", used_car_sales: "Gebrauchtwagen", new_car_sales: "Neuwagen",
    rental: "Vermietung", cleaning: "Reinigung", detailing: "Aufbereitung", diy: "Selbsthilfe", smart_repair: "Smart-Repair",
  };
  const SERVICE_KIND = { repair: "Kfz-Werkstatt", truck_repair: "Lkw-Werkstatt", caravan: "Caravan-Händler/Service",
    gas: "Gasflaschen", laundry: "Waschsalon" };

  /** Merkmale -> [Chip-Text]; group: camp | service | shop | food | sight */
  function services(group, f) {
    f = f || {};
    if (group === "camp") {
      return [f.power && "🔌 Strom", f.water && "🚰 Wasser", f.dump && "🚽 Entsorgung", f.drinking_water === true && "Trinkwasser",
        f.toilets && "🚻 WC", f.shower && "🚿 Dusche", f.wifi && "📶 WLAN", f.laundry && "🧺 Waschmaschine",
        (f.dog === "yes" || f.dog === "leashed") && `🐕 Hunde${f.dog === "leashed" ? " (an der Leine)" : ""}`,
        f.dog === "no" && "keine Hunde", f.fee === false && "💶 kostenlos", f.caravans === false && "keine Wohnwagen",
        f.wheelchair === true && "♿ barrierefrei"].filter(Boolean);
    }
    if (group === "service") {
      const out = (f.services || []).map((k) => VEHICLE_SERVICE[k]).filter(Boolean);
      if (f.gas_exchange) out.push("🔥 Gasflaschen-Tausch");
      if (f.self_service) out.push("Selbstbedienung");
      return [...new Set(out)];
    }
    return [];
  }

  /** Telefonnummer -> tel:-Link (nur Ziffern und +), "" wenn zu kurz */
  function telHref(phone) {
    const first = String(phone || "").split(/[;,]/)[0];
    const n = first.replace(/[^\d+]/g, "");
    return n.replace(/\D/g, "").length >= 6 ? `tel:${n}` : "";
  }
  const webHref = (u) => (/^https?:\/\/[^\s]+$/i.test(String(u || "").trim()) ? String(u).trim() : "");
  const mailHref = (m) => (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(m || "").trim()) ? `mailto:${String(m).trim()}` : "");

  /** Langer Text -> {short, rest}: an einer Satz-/Wortgrenze nach etwa n Zeichen kuerzen */
  function trim(text, n = 160) {
    const t = String(text || "").trim();
    if (t.length <= n + 30) return { short: t, rest: "" };
    let cut = t.lastIndexOf(". ", n);
    if (cut < n * 0.5) cut = t.lastIndexOf(" ", n);
    if (cut < 20) cut = n;
    return { short: t.slice(0, cut + 1).trim(), rest: t.slice(cut + 1).trim() };
  }

  /** Wikimedia-Commons-Datei aus OSM (wikimedia_commons=File:… oder Category:…) -> Dateiname oder "" */
  function commonsFile(v) {
    const m = /^(?:File|Datei):(.+\.(?:jpe?g|png|webp|gif|tiff?))$/i.exec(String(v || "").trim());
    return m ? m[1].trim() : "";
  }

  /** Kurzform der Zeiten fuer den Kopf der Karte: {state: "open"|"closed"|"unknown", text} */
  function hoursLine(hours, seasonal, now) {
    const C = root.CampFilter;
    const open = C.isOpen(hours, seasonal, now);
    const week = C.weekHours(hours, now);
    const today = week ? week[0].text : "";
    if (open === true) {
      const c = C.nextChange(hours, now);
      return { state: "open", text: `geöffnet${c && c.open ? ` bis ${c.at}` : ""}${today && today !== "24 Stunden" ? ` · heute ${today}` : today ? " · 24 Stunden" : ""}` };
    }
    if (open === false) {
      const c = C.nextChange(hours, now);
      return { state: "closed", text: `geschlossen${c && !c.open ? ` · öffnet ${c.at}` : ""}${today && today !== "geschlossen" ? ` · heute ${today}` : ""}` };
    }
    return { state: "unknown", text: hours ? "Öffnungszeiten prüfen" : "" };
  }

  /** OSM-Saison „Mar-Nov“ / „May,Jun“ -> deutsch „Mär–Nov“ */
  const MON_DE = { Jan: "Jan", Feb: "Feb", Mar: "Mär", Apr: "Apr", May: "Mai", Jun: "Jun", Jul: "Jul", Aug: "Aug", Sep: "Sep",
    Oct: "Okt", Nov: "Nov", Dec: "Dez" };
  const seasonDe = (s) => String(s || "").replace(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/g, (m) => MON_DE[m]).replace(/-/g, "–").replace(/,/g, ", ");

  const api = { seasonDe, services, telHref, webHref, mailHref, trim, commonsFile, hoursLine, VEHICLE_SERVICE, SERVICE_KIND };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  /* ---- Browser ---- */
  const el = (tag, props, ...kids) => {
    const n = document.createElement(tag);
    Object.assign(n, props || {});
    for (const k of kids) if (k != null && k !== false && k !== "") n.append(k);
    return n;
  };
  const link = (href, text, cls, ext = true) => {
    const a = el("a", { href, textContent: text, className: cls || "" });
    if (ext && /^https?:/.test(href)) { a.target = "_blank"; a.rel = "noopener"; }
    return a;
  };
  const de = (v, d = 0) => Number(v).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });

  /**
   * p: Ort mit {name, lat, lon, km, off, osm, flags}; o:
   *   icon, title, chips: [Text], rating: {avg, n}, meta: Text (statt km/abseits), group (camp|service|…),
   *   phone, website, email, hours, seasonal, desc, actions: [Element|HTML-Text] (z. B. „In die Tour“),
   *   more: [{href, text}], photos: [{src, alt, href, credit}], mly: true (Einfahrt-Foto nachladen),
   *   top: [{html}|Element] (direkt unter den Knoepfen), extra: [{html}|Element] (zwischen Zeiten und Beschreibung)
   */
  function card(p, o) {
    const now = new Date();
    const box = el("div", { className: "dcard camppop" });
    const head = el("div", { className: "dc-head" }, el("b", { className: "dc-title", textContent: `${o.icon ? o.icon + " " : ""}${o.title}` }));
    const chips = el("div", { className: "dc-chips" });
    for (const c of o.chips || []) chips.append(el("span", { className: "dc-chip", textContent: c }));
    if (o.rating && o.rating.n) chips.append(el("span", { className: "dc-chip dc-star", textContent: `★ ${de(o.rating.avg, 1)} (${o.rating.n})` }));
    if (chips.childElementCount) head.append(chips);
    const meta = o.meta != null ? o.meta : [p.km != null && `bei km ${de(p.km, 1)}`, p.off != null && `${de(p.off)} m abseits`].filter(Boolean).join(" · ");
    if (meta) head.append(el("small", { className: "dc-meta", textContent: meta }));
    box.append(head);
    const bar = el("div", { className: "dc-actions" });
    if (typeof appHref === "function") {
      const nav = link(appHref(p.lat, p.lon, o.title), "🧭 Navigieren", "dc-btn primary", !(typeof IS_ANDROID !== "undefined" && IS_ANDROID));
      bar.append(nav);
    }
    const tel = telHref(o.phone);
    if (tel) bar.append(link(tel, "📞 Anrufen", "dc-btn", false));
    const web = webHref(o.website);
    if (web) bar.append(link(web, "🌐 Website", "dc-btn"));
    for (const a of o.actions || []) {
      if (!a) continue;
      if (typeof a === "string") bar.insertAdjacentHTML("beforeend", a);
      else bar.append(a);
    }
    const more = el("details", { className: "dc-more" }, el("summary", { textContent: "⋯ Mehr" }));
    const ml = el("div", { className: "dc-morelist" });
    const mail = mailHref(o.email);
    if (mail) ml.append(link(mail, "✉ E-Mail", "", false));
    for (const m of o.more || []) ml.append(link(m.href, m.text));
    if (p.osm && /^(node|way|relation)\/\d+$/.test(p.osm)) ml.append(link(`https://www.openstreetmap.org/${p.osm}`, "In OpenStreetMap ansehen"));
    const ll = `${(+p.lat).toFixed(5)}, ${(+p.lon).toFixed(5)}`;
    const cp = el("button", { type: "button", className: "coord", textContent: `📋 ${ll}`, title: "Koordinaten kopieren" });
    cp.dataset.copy = ll;
    ml.append(cp);
    more.append(ml);
    bar.append(more);
    box.append(bar);
    // wichtige Bloecke direkt unter den Knoepfen (Stellplatz: passt es? Laenge)
    for (const x of o.top || []) {
      if (!x) continue;
      if (x.html) box.insertAdjacentHTML("beforeend", x.html);
      else box.append(x);
    }
    const strip = el("div", { className: "dc-photos" });
    for (const ph of o.photos || []) {
      const img = el("img", { src: ph.src, alt: ph.alt || "", loading: "lazy" });
      const fig = el("figure", {}, ph.href ? link(ph.href, "", "dc-ph") : el("span", { className: "dc-ph" }));
      fig.firstChild.append(img);
      if (ph.credit) fig.append(el("figcaption", { textContent: ph.credit }));
      strip.append(fig);
    }
    if (o.mly) {
      const m = el("div", { className: "mlyph" });
      m.dataset.mly = `${p.lat},${p.lon}`;
      strip.append(m);
    }
    if (strip.childElementCount) box.append(strip);
    if (o.hours || o.seasonal) {
      const h = hoursLine(o.hours, o.seasonal, now);
      const week = root.CampFilter.weekHours(o.hours, now);
      const dot = { open: "🟢", closed: "🔴", unknown: "🕒" }[h.state];
      const line = `${dot} ${h.text || "Öffnungszeiten"}${o.seasonal && o.seasonal !== "no" && o.seasonal !== "yes" ? ` · Saison ${seasonDe(o.seasonal)}` : ""}`;
      if (week) {
        const d = el("details", { className: "dc-hours" }, el("summary", { textContent: line }));
        const t = el("table");
        for (const w of week) t.append(el("tr", {}, el("th", { textContent: w.day }), el("td", { textContent: w.text })));
        d.append(t);
        box.append(d);
      } else {
        box.append(el("div", { className: "dc-hours" }, line), o.hours ? el("small", { className: "dc-raw", textContent: o.hours }) : null);
      }
    }
    for (const x of o.extra || []) {
      if (!x) continue;
      if (x.html) box.insertAdjacentHTML("beforeend", x.html);
      else box.append(x);
    }
    if (o.desc) {
      const t = trim(o.desc);
      if (t.rest) {
        const d = el("details", { className: "dc-desc" }, el("summary", {}, el("i", { textContent: `${t.short} ` }), el("span", { className: "dc-mehr", textContent: "mehr" })));
        d.append(el("i", { textContent: t.rest }));
        box.append(d);
      } else box.append(el("p", { className: "dc-desc" }, el("i", { textContent: t.short })));
    }
    const sv = o.services || services(o.group, p.flags);
    if (sv.length) {
      const s = el("div", { className: "dc-services" });
      if (o.servicesTitle) s.append(el("small", { className: "dc-stitle", textContent: o.servicesTitle }));
      for (const c of sv) s.append(el("span", { className: "dc-chip soft", textContent: c }));
      box.append(s);
    }
    if (o.note) box.append(el("small", { className: "dc-note", textContent: o.note }));
    if (p.osm && /^(node|way|relation)\/\d+$/.test(p.osm) && o.osmLink !== false) {
      box.append(el("div", { className: "dc-foot" }, link(`https://www.openstreetmap.org/edit?${p.osm.replace("/", "=")}`, "✏ Fehlt was? In OSM ergänzen", "dc-edit")));
    }
    return box;
  }

  /** Wikimedia-Foto ueber den eigenen Server (Datenschutz: der Browser laedt nichts bei Wikimedia) */
  function commonsPhoto(v) {
    const f = commonsFile(v);
    return f ? { src: `/api/foto/commons?datei=${encodeURIComponent(f)}`, alt: f, credit: "Foto: Wikimedia Commons",
      href: `https://commons.wikimedia.org/wiki/File:${encodeURIComponent(f.replace(/ /g, "_"))}` } : null;
  }

  root.DetailCard = { ...api, card, commonsPhoto };
})(typeof window !== "undefined" ? window : globalThis);
