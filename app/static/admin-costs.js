/* Admin „Kosten“ (RigAnalyzer) – Kosten je Account in drei Szenarien.
   Reine Funktionen (bars, scale, eur) per require testbar; im Browser window.AdminCosts + Seitenlogik. */
(function (root) {
  const eur = (x) => {
    const n = Number(x) || 0;
    return (n < 0 ? "-" : "") + Math.abs(n).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
  };
  const num = (x, d) => (Number(x) || 0).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });

  // Skala: groesster Balken (Kosten + Gewinn) oder Preis ohne Abo (Marker bleibt im Bild), mindestens der Abo-Preis
  function scale(scenario, abo) {
    let m = Number(abo) || 0;
    for (const a of (scenario && scenario.accounts) || []) m = Math.max(m, a.cost + Math.max(a.profit_abo, 0), a.active ? a.price_payg || 0 : 0);
    return m;
  }

  // Balken in Prozent der Skala: Verbrauch | Anteil | Gewinn bzw. Verlust (Verlust liegt in den Kosten)
  function bars(accounts, maxEur) {
    const pc = (v) => (maxEur > 0 ? (v / maxEur) * 100 : 0);
    return accounts.map((a) => {
      if (!a.active || !(a.cost > 0)) return { name: a.name, segs: [], marker: null };
      const v = pc(a.variable), c = pc(a.cost), segs = [];
      if (v > 0) segs.push({ kind: "var", from: 0, to: v });
      if (c > v) segs.push({ kind: "share", from: v, to: c });
      if (a.profit_abo > 0) segs.push({ kind: "profit", from: c, to: pc(a.cost + a.profit_abo) });
      else if (a.profit_abo < 0) segs.push({ kind: "loss", from: pc(a.cost + a.profit_abo), to: c });
      return { name: a.name, segs, marker: pc(a.price_payg) };
    });
  }

  // Summenzeile: Zellen mit Spaltenbreite, zusammen COLS (= Spalten der Tabelle); jede Summe unter ihrer Ueberschrift
  const COLS = 12;
  function summaryCells(t) {
    return [
      { text: "Summe Rig-Accounts", span: 1, th: true },
      { text: "", span: 7 },                                               // Download .. Anteil, Verbrauch
      { text: eur(t.cost), span: 1, cls: "n" },                           // Kosten
      { text: eur(t.profit_abo), span: 1, cls: "n" + (t.profit_abo < 0 ? " neg" : "") },  // Gewinn mit Abo
      { text: "", span: 1 },                                              // Preis o. Abo
      { text: eur(t.profit_payg), span: 1, cls: "n" + (t.profit_payg < 0 ? " neg" : "") }, // Gewinn ohne Abo
    ];
  }

  // Preisformular -> Request: leere Zahlenfelder weglassen (Number("") waere 0)
  function priceBody(inputs) {
    const body = {};
    for (const i of inputs) {
      if (i.type === "number") { if (String(i.value).trim() !== "") body[i.name] = Number(i.value); }
      else body[i.name] = i.value;
    }
    return body;
  }
  const breakEvenText = (t) => t.break_even ? `Gewinnschwelle: ${t.break_even} zahlende Accounts.` : "Gewinnschwelle: nicht erreichbar.";

  // Entwicklung (Claude) je Login: Zeilen fuer den Abschnitt, Logins nach Euro absteigend
  const APPNAME = { ride: "Ride", rig: "Rig" };
  function devRows(dev) {
    if (!dev || !Array.isArray(dev.logins)) return [];
    return [...dev.logins].sort((a, b) => (Number(b.eur) || 0) - (Number(a.eur) || 0)).map((l) => ({
      login: l.login, eur: eur(l.eur),
      tickets: (l.tickets || []).map((t) => ({
        text: `#${t.nr} · ${APPNAME[t.app] || t.app} · ${t.status} · ${t.titel}`, eur: eur(t.eur) })),
    }));
  }

  const api = { bars, scale, eur, summaryCells, COLS, priceBody, breakEvenText, devRows };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AdminCosts = api;
  if (typeof document === "undefined") return;

  // ---- Browser ----
  const LABELS = {
    strom_ct: ["Strom (ct/kWh)", 0.1], server_w: ["Server (Watt)", 0.1], proxy_w: ["Proxy (Watt)", 0.1],
    hw_eur: ["Hardware (€/Monat)", 0.01], up_mbit: ["Upload (Mbit/s)", 0.1], down_mbit: ["Download (Mbit/s)", 0.1],
    s3_name: ["S3-Anbieter", null], s3_base_eur: ["S3 Grundpreis (€/Monat)", 0.01], s3_incl_gb: ["S3 Speicher inkl. (GB)", 1],
    s3_egress_incl_gb: ["S3 Traffic inkl. (GB)", 1], s3_extra_eur_tb: ["S3 Speicher extra (€/TB)", 0.01],
    s3_egress_eur_tb: ["S3 Traffic extra (€/TB)", 0.01], cloud_name: ["Cloud-Anbieter", null],
    cloud_eur: ["Cloud (€/Monat)", 0.01], abo_eur: ["Abo-Preis (€/Monat)", 0.01], marge_pct: ["Marge ohne Abo (%)", 0.1],
    fee_pct: ["Zahlungsgebühr (%)", 0.01], fee_fix_eur: ["Zahlungsgebühr fix (€)", 0.01],
    usd_eur: ["Kurs USD → € (Entwicklung)", 0.0001],
  };
  const dateDe = (iso) => { const d = new Date(iso); return iso && !Number.isNaN(d.getTime()) ? d.toLocaleDateString("de-DE") : "–"; };
  const monthName = (m) => new Date(m + "-15T12:00:00").toLocaleDateString("de-DE", { month: "long", year: "numeric" });
  const KIND = { var: "Verbrauch", share: "Anteil Fixkosten", profit: "Gewinn", loss: "Verlust" };
  const csrf = () => (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
  async function call(method, url, body) {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf() },
      body: body ? JSON.stringify(body) : undefined });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(typeof j.detail === "string" ? j.detail : `Fehler ${r.status}`); e.status = r.status; throw e; }
    return j;
  }
  const el = (tag, attrs, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
    }
    for (const k of kids) n.append(k);
    return n;
  };

  const card = document.getElementById("cost-card");
  if (!card) return;
  let data = null, scen = "home", month = "";

  function scenLabel(k) {
    return k === "home" ? "Zu Hause" : k === "home_s3" ? "Zu Hause + " + data.prices.s3_name : "Cloud (" + data.prices.cloud_name + ")";
  }

  function render() {
    const sc = data.scenarios[scen], abo = data.prices.abo_eur, S = scale(sc, abo) || 1;
    const head = card.querySelector("#cost-head"); head.replaceChildren();
    const tabs = el("div", { class: "cost-tabs", role: "group", "aria-label": "Szenario" });
    for (const k of ["home", "home_s3", "cloud"]) {
      const b = el("button", { type: "button", class: "ghost small", "aria-pressed": String(k === scen), text: scenLabel(k) });
      b.onclick = () => { scen = k; render(); };
      tabs.append(b);
    }
    const sel = el("select", { "aria-label": "Monat" });
    for (const m of data.months) { const o = el("option", { value: m, text: monthName(m) }); o.selected = m === data.month; sel.append(o); }
    sel.onchange = () => load(sel.value);
    head.append(tabs, sel);
    card.querySelector("#cost-since").textContent = `Zählung seit ${dateDe(data.since)}. Preise sind Richtwerte (Stand Mitte 2026).`;

    const bs = bars(sc.accounts, S), chart = card.querySelector("#cost-bars"); chart.replaceChildren();
    sc.accounts.forEach((a, i) => {
      const b = bs[i], track = el("div", { class: "cost-track" });
      for (const s of b.segs) {
        const seg = el("span", { class: "cost-seg " + s.kind, title: `${a.name}: ${KIND[s.kind]}` });
        seg.style.left = s.from + "%"; seg.style.width = Math.max(s.to - s.from, 0) + "%";
        track.append(seg);
      }
      if (b.marker !== null) {
        const m = el("span", { class: "cost-mark", title: `${a.name}: Preis ohne Abo ${eur(a.price_payg)}` });
        m.style.left = Math.min(b.marker, 100) + "%"; track.append(m);
      }
      chart.append(el("div", { class: "cost-row" + (a.active ? "" : " off") },
        el("span", { class: "cost-name", text: a.name + (a.active ? "" : " (inaktiv)") }), track,
        el("span", { class: "cost-val", text: a.active ? eur(a.cost) : "inaktiv" })));
    });
    const leg = card.querySelector("#cost-legend"); leg.replaceChildren();
    for (const k of ["var", "share", "profit", "loss"])
      leg.append(el("span", { class: "cost-key" }, el("i", { class: "cost-seg " + k }), KIND[k]));
    leg.append(el("span", { class: "cost-key" }, el("i", { class: "cost-mark" }), "Preis ohne Abo"));
    leg.append(el("span", { class: "cost-key", text: `Skala bis ${eur(S)}` }));

    const tb = card.querySelector("#cost-rows"); tb.replaceChildren();
    const td = (t, c) => el("td", c ? { class: c, text: t } : { text: t });
    for (const a of sc.accounts) {
      const u = a.usage, tr = el("tr", a.active ? {} : { class: "off" });
      tr.append(td(a.name + (a.active ? "" : " (inaktiv)")), td(num(u.dl_gb, 2), "n"), td(num(u.storage_gb, 2), "n"),
        td(String(u.analyses), "n"), td(String(u.plans), "n"), td(String(u.routes), "n"), td(eur(a.share), "n"),
        td(eur(a.variable), "n"), td(eur(a.cost), "n"), td(eur(a.profit_abo), "n" + (a.profit_abo < 0 ? " neg" : "")),
        td(a.active ? eur(a.price_payg) : "–", "n"),
        td(a.active ? eur(a.profit_payg) : "–", "n" + (a.active && a.profit_payg < 0 ? " neg" : "")));
      tb.append(tr);
    }
    const t = sc.totals, tf = card.querySelector("#cost-foot"); tf.replaceChildren();
    const sum = el("tr");
    for (const c of summaryCells(t)) {
      const n = el(c.th ? "th" : "td", c.cls ? { class: c.cls } : {});
      n.textContent = c.text; if (c.span > 1) n.colSpan = c.span;
      sum.append(n);
    }
    tf.append(sum);
    const info = [`Fixkosten ${eur(sc.fixed)}/Monat, verteilt auf ${sc.active} aktive Accounts (Ride + Rig).`,
      `Abo-Einnahmen ${eur(t.revenue_abo)}.`,
      breakEvenText(t)];
    if (scen === "home") info.push(`Upload ${num(sc.upload_h, 1)} h = ${num((sc.upload_h / 730) * 100, 1)} % von 730 h.`);
    card.querySelector("#cost-sum").textContent = info.join(" ");
    renderDev();
  }

  // Entwicklung (Claude): Kosten des Kommentar-Bots je Login, aus dem Rueckmeldungs-Abgleich (devcosts.json)
  function renderDev() {
    const box = card.querySelector("#cost-dev"); if (!box) return;
    box.replaceChildren(el("h4", { text: "Entwicklung (Claude)" }));
    if (!data.dev) { box.append(el("p", { class: "hint", text: "Noch kein Abgleich – Forgejo-Ablauf „rueckmeldungen-sync“ von Hand starten." })); return; }
    const rows = devRows(data.dev);
    if (!rows.length) box.append(el("p", { class: "hint", text: "In diesem Monat keine Läufe zu Rückmeldungen." }));
    for (const r of rows) {
      const list = el("ul", {}, ...r.tickets.map((t) => el("li", {}, el("span", { text: t.text }), el("b", { text: " " + t.eur }))));
      box.append(el("details", { class: "dev-login" }, el("summary", {}, el("span", { text: r.login }), el("b", { text: " " + r.eur })), list));
    }
    box.append(el("p", { class: "hint", text: `Stand des Abgleichs: ${dateDe(data.dev.stand)} · Kurs ${data.prices.usd_eur} € je USD.` }));
  }

  function renderPrices() {
    const box = card.querySelector("#cost-prices"); box.replaceChildren();
    for (const [k, [label, step]] of Object.entries(LABELS)) {
      if (!(k in data.prices)) continue;
      const inp = el("input", { name: k, value: String(data.prices[k]) });
      if (step) { inp.type = "number"; inp.step = String(step); inp.min = "0"; } else inp.type = "text";
      box.append(el("label", {}, label, inp));
    }
  }

  async function savePrices() {
    const msg = card.querySelector("#cost-msg");
    const body = priceBody(card.querySelectorAll("#cost-prices input"));
    try { await call("PUT", "/api/admin/costs/prices", body); msg.textContent = "Gespeichert."; msg.className = "lg-ok"; await load(month); }
    catch (e) { msg.textContent = e.message; msg.className = "lg-err"; }
    msg.hidden = false;
  }

  async function load(m) {
    try {
      data = await call("GET", "/api/admin/costs" + (m ? "?month=" + encodeURIComponent(m) : ""));
      month = data.month; card.hidden = false; renderPrices(); render();
    } catch (e) {
      if (e.status === 404) card.hidden = true;
      else card.querySelector("#cost-since").textContent = e.message;
    }
  }

  card.querySelector("#cost-save").onclick = savePrices;
  // admin.js blendet .full-only fuer Gruppen-Admins aus; erst nach dessen /api/me laden
  const start = () => { if (!card.hidden) load(""); };
  const probe = setInterval(() => {
    if (card.hidden) return;
    clearInterval(probe); start();
  }, 150);
  setTimeout(() => clearInterval(probe), 8000);
})(typeof window !== "undefined" ? window : globalThis);
