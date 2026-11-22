/* Admin – Logins verwalten. Alle Aenderungen mit CSRF-Header (Cookie ra_csrf). */
(function () {
  const $ = (id) => document.getElementById(id);
  const csrf = () => (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
  const el = (tag, props = {}, ...kids) => {
    const n = Object.assign(document.createElement(tag), props);
    kids.forEach((k) => n.append(k));
    return n;
  };

  async function api(method, url, body) {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf() },
      body: body ? JSON.stringify(body) : undefined });
    const j = r.status === 204 ? {} : await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `Fehler ${r.status}`);
    return j;
  }

  function msg(text, bad) {
    const m = $("adm-msg");
    m.textContent = text;
    m.className = bad ? "lg-err" : "lg-ok";
    m.hidden = !text;
  }

  function showPassword(title, name, pw, extra) {
    const d = $("adm-pw");
    $("adm-pw-title").textContent = title;
    $("adm-pw-name").textContent = name;
    $("adm-pw-value").textContent = pw;
    $("adm-pw-extra").textContent = extra || "";
    d.showModal();
  }

  const fmt = (iso) => (iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–");

  let activity = { users: [], logins: {} };
  let full = true;                                   // false = Gruppen-Admin: nur Logins seiner Gruppen
  let scope = { apps: ["ride", "rig"], groups: [] };
  let groups = [];
  const gname = (id) => (groups.find((g) => g.id === id) || {}).name || "–";
  const APPNAME = { ride: "Ride", rig: "Rig" };
  // Admin je App: diese Seite zeigt und schaltet nur ihre eigene App, den Rest fuehrt der Server zusammen
  const HOST_APP = location.hostname.startsWith("rig") ? "rig" : "ride";
  const OTHER = HOST_APP === "rig" ? "ride" : "rig";
  const rightsText = (a) => (a === true ? "Oberadmin" : a && a.apps.includes(HOST_APP)
    ? `${APPNAME[HOST_APP]} · ${a.groups.map(gname).join(", ") || "keine Gruppe"}` : "–");

  let rightsFor = null, rightsUser = null;
  function openRights(u) {
    rightsFor = u.name; rightsUser = u;
    const f = $("adm-rights-form");
    $("adm-rights-title").textContent = `Admin-Rechte von ${u.name}`;
    const a = u.admin;
    f.elements.ober.checked = a === true;
    f.elements[HOST_APP].checked = !!(a && a !== true && a.apps.includes(HOST_APP));
    const box = $("adm-rights-groups");
    box.replaceChildren(...groups.map((g) => el("label", { className: "pill" },
      el("input", { type: "checkbox", value: g.id, checked: !!(a && a !== true && a.groups.includes(g.id)) }), ` ${g.name} `)));
    if (!groups.length) box.append(el("span", { className: "hint", textContent: "Noch keine Gruppen – erst unter „Gruppen“ anlegen." }));
    const sync = () => {
      $("adm-rights-sub").hidden = f.elements.ober.checked;
      const sub = !f.elements.ober.checked && f.elements[HOST_APP].checked;
      const none = !box.querySelector("input:checked");
      $("adm-rights-warn").hidden = !(sub && none);
      f.querySelector("button[type=submit]").disabled = sub && none;   // Gruppen-Admin ohne Gruppe sieht niemanden
    };
    f.onchange = sync;
    sync();
    $("adm-rights").showModal();
  }

  async function loadGroups() {
    groups = await api("GET", "/api/admin/groups");
    const sel = $("adm-new-group");
    const opts = (full ? [["", "– keine –"]] : []).concat(groups.map((g) => [g.id, g.name]));
    sel.replaceChildren(...opts.map(([v, t]) => el("option", { value: v, textContent: t })));
    if (!full) return;
    const list = $("grp-list");
    list.replaceChildren();
    if (!groups.length) list.append(el("p", { className: "hint", textContent: "Noch keine Gruppen." }));
    groups.forEach((g) => {
      const ren = el("button", { type: "button", className: "ghost small", textContent: "✏ Umbenennen" });
      ren.addEventListener("click", () => {
        const v = prompt(`Neuer Name für „${g.name}“`, g.name);
        if (v) act(() => api("PATCH", `/api/admin/groups/${g.id}`, { name: v }), "Gruppe umbenannt");
      });
      const del = el("button", { type: "button", className: "ghost small", textContent: "🗑", title: "Gruppe löschen" });
      del.addEventListener("click", () => {
        if (confirm(`Gruppe „${g.name}“ löschen? Die ${g.members} Logins bleiben, nur ohne Gruppe.`))
          act(() => api("DELETE", `/api/admin/groups/${g.id}`), "Gruppe gelöscht");
      });
      list.append(el("div", { className: "act-item" }, el("b", { textContent: g.name }),
        el("span", { className: "sub", textContent: ` · ${g.members} Login${g.members === 1 ? "" : "s"} ` }), ren, " ", del));
    });
  }
  const ago = (t) => {
    if (!t) return "nie";
    const s = Math.max(0, Date.now() / 1000 - t);
    if (s < 90) return "gerade eben";
    if (s < 3600) return `vor ${Math.round(s / 60)} min`;
    if (s < 86400) return `vor ${Math.round(s / 3600)} h`;
    return new Date(t * 1000).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
  };
  const STATE = { online: "online", heute: "heute", frueher: "länger her", nie: "nie" };
  const ipText = (u) => (u.ip ? `${u.ip}${u.host ? " · " + u.host.split(".").slice(-3).join(".") : ""}` : "");

  function naviText(n) {
    if (n.idle) return `📍 RigNavi offen, ohne Ziel${n.stale ? ` · zuletzt ${ago(n.t)}` : ""}`;
    const bits = [`🚐 fährt nach ${n.dest || "?"}`];
    if (n.remaining_m != null) bits.push(`noch ${Math.round(n.remaining_m / 1000)} km`);
    if (n.eta_s != null) bits.push(`an ${new Date(Date.now() + n.eta_s * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`);
    if (n.source) bits.push(n.source === "package" ? "📦 offline-Paket" : "☁ Server");
    if (n.stale) bits.push(`zuletzt ${ago(n.t)}`);
    return bits.join(" · ");
  }

  let map = null, markers = null;
  function drawActivity() {
    const list = $("act-list");
    const active = activity.users.filter((u) => u.state === "online" || u.navi);
    list.replaceChildren();
    if (!active.length) list.append(el("p", { className: "hint", textContent: "Gerade niemand aktiv." }));
    active.forEach((u) => {
      const head = el("div", {}, el("span", { className: `dot ${u.state}` }), el("b", { textContent: u.name }));
      const what = u.navi ? naviText(u.navi) : (u.action || "angemeldet");
      list.append(el("div", { className: "act-item" }, head, el("div", { textContent: what }),
        el("div", { className: "sub", textContent: `${u.device || ""} · ${ipText(u)} · ${ago(u.seen)}` })));
    });
    if (!map && window.L && $("act-map")) {   // Karte (RigNavi-Positionen) nur beim RigAnalyzer
      map = L.map("act-map", { zoomControl: true, attributionControl: true }).setView([47.5, 9], 5);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 15, attribution: "© OpenStreetMap",
        referrerPolicy: "strict-origin-when-cross-origin" }).addTo(map);   // ohne Referer blockt OSM (403)
      markers = L.layerGroup().addTo(map);
      // Dashboard: Kachel halb/ganz breit, andere Spalte, aufgeklappt -> Karte neu vermessen (sonst graue Kacheln)
      const fit = () => map.invalidateSize();
      if (window.ResizeObserver) new ResizeObserver(fit).observe($("act-map"));
      document.addEventListener("cards:layout", () => requestAnimationFrame(fit));
    }
    if (markers) {
      markers.clearLayers();
      const pts = activity.users.filter((u) => u.navi && u.navi.lat != null);
      pts.forEach((u) => L.circleMarker([u.navi.lat, u.navi.lon], { radius: 9, weight: 3, color: "#fff",
        fillColor: u.navi.stale ? "#999" : "#17624f", fillOpacity: 1 }).bindTooltip(`${u.name}: ${naviText(u.navi)}`).addTo(markers));
      if (pts.length) map.fitBounds(L.latLngBounds(pts.map((u) => [u.navi.lat, u.navi.lon])).pad(0.6), { maxZoom: 10 });
    }
    $("act-upd").textContent = `aktualisiert ${new Date().toLocaleTimeString("de-DE")}`;
  }

  function showHistory(name) {
    $("adm-hist-title").textContent = `Anmeldungen von ${name}`;
    const rows = $("adm-hist-rows");
    rows.replaceChildren();
    (activity.logins[name] || []).forEach((l) => rows.append(el("tr", {},
      el("td", { textContent: new Date(l.t * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) }),
      el("td", { textContent: l.ip }), el("td", { textContent: l.device }),
      el("td", {}, el("span", { className: `pill ${l.ok ? "ok" : "bad"}`, textContent: l.ok ? "ok" : "Fehlversuch" })))));
    if (!rows.children.length) rows.append(el("tr", {}, el("td", { colSpan: 4, textContent: "Noch keine Anmeldung protokolliert." })));
    $("adm-hist").showModal();
  }

  async function loadActivity() {
    try { activity = await api("GET", "/api/admin/activity"); drawActivity(); } catch { /* naechster Versuch */ }
  }

  async function load() {
    const me = await api("GET", "/api/me");
    full = me.admin === true;
    scope = full ? { apps: ["ride", "rig"], groups: [] } : me.admin;
    document.querySelectorAll(".full-only").forEach((n) => { n.hidden = !full; });

    await loadGroups();
    await loadActivity();
    const users = await api("GET", "/api/admin/users");
    const tb = $("adm-rows");
    tb.replaceChildren();
    users.sort((a, b) => (b.pending ? 1 : 0) - (a.pending ? 1 : 0));   // offene Antraege nach oben
    // eine Zeile je Login (Überblick), Details zum Aufklappen – immer nur einer offen, bleibt nach Aktionen offen
    users.forEach((u) => {
      const a = activity.users.find((x) => x.name === u.name) || { state: "nie" };
      const fails = (activity.logins[u.name] || []).filter((l) => !l.ok).length;
      const pill = (cls, text, title) => el("span", { className: `pill ${cls}`, textContent: text, title: title || "" });

      // ---- Kopfzeile: Punkt, Name, Hinweise, Gruppe, zuletzt
      const flags = el("span", { className: "al-flags" });
      if (u.pending) flags.append(pill("warn", "wartet auf Freischaltung", u.registered ? `selbst registriert am ${fmt(u.registered)}` : "selbst registriert"));
      else if (u.locked) flags.append(pill("bad", "gesperrt"));
      if (u.pw_initial) flags.append(pill("warn", "Startpasswort", "Noch das vom Admin vergebene Passwort"));
      if (fails) flags.append(pill("bad", `${fails} Fehlversuch${fails > 1 ? "e" : ""}`));
      if (u.admin) flags.append(pill("", `🛡 ${u.admin === true ? "Oberadmin" : "Gruppen-Admin"}`));
      // bestellte Mails – nur anzeigen, schalten kann nur der Nutzer selbst (Mein Konto)
      const nt = u.notify || {};
      if (nt.newsletter) flags.append(pill("ok", "📰 Newsletter", "will den Newsletter"));
      if (nt.releases) flags.append(pill("ok", "✉️ Versionen", "will die abendliche Sammelmail „Neue Versionen“"));
      const head = el("summary", { className: "al-head" },
        el("span", { className: `dot ${a.state}`, title: STATE[a.state] }),
        el("b", { className: "al-name", textContent: u.name }), flags,
        el("span", { className: "al-grp", textContent: u.group ? gname(u.group) : "" }),
        el("span", { className: "al-when", textContent: a.state === "nie" ? "noch nie aktiv" : ago(a.seen) }));
      const lock = el("button", { type: "button", className: u.pending ? "primary small" : "ghost small",
        textContent: u.pending ? "✅ Freischalten" : u.locked ? "🔓 Entsperren" : "🔒 Sperren" });
      lock.addEventListener("click", (e) => {
        e.preventDefault();                                 // im Kopf (Freischalten): nicht auf-/zuklappen
        openName = u.name;
        act(() => api("PATCH", `/api/admin/users/${u.name}`, { locked: !u.locked }),
          u.pending ? `${u.name} freigeschaltet${u.mail ? " – Mail ist unterwegs" : ""}` : u.locked ? `${u.name} entsperrt` : `${u.name} gesperrt`);
      });
      if (u.pending) head.append(lock);                     // Freischalten ist die eine Aktion, die gleich sichtbar sein muss

      // ---- Details
      const mail = el("button", { type: "button", className: "ghost small", textContent: u.mail || "Mail eintragen" });
      mail.addEventListener("click", () => {
        const v = prompt(`Mailadresse für ${u.name} (leer = keine)`, u.mail || "");
        if (v !== null) act(() => api("PATCH", `/api/admin/users/${u.name}`, { mail: v }), "Mail gespeichert");
      });
      const grpOpts = (full ? [["", "–"]] : []).concat(groups.map((g) => [g.id, g.name]));
      const grp = el("select", { disabled: !full && groups.length < 2, title: "Gruppe" },
        ...grpOpts.map(([v, t]) => el("option", { value: v, textContent: t, selected: v === (u.group || "") })));
      grp.addEventListener("change", () => act(() => api("PATCH", `/api/admin/users/${u.name}`, { group: grp.value || null }),
        `${u.name}: Gruppe ${grp.value ? gname(grp.value) : "entfernt"}`));
      const apps = el("span");
      scope.apps.filter((x) => x === HOST_APP).forEach((x) => {
        const cb = el("input", { type: "checkbox", checked: u.apps.includes(x), title: x === "ride" ? "RideAnalyzer" : "RigAnalyzer" });
        cb.addEventListener("change", () => act(() => api("PATCH", `/api/admin/users/${u.name}`,
          { apps: cb.checked ? [x] : [] }), "Zugang gespeichert"));   // andere App fuehrt der Server mit
        apps.append(el("label", { className: "pill" }, cb, ` ${x === "ride" ? "Ride" : "Rig"}`));
      });
      const access = [mail, grp, apps];
      if (full) {
        const adm = el("button", { type: "button", className: "ghost small", textContent: `🛡 ${rightsText(u.admin)}`, title: "Admin-Rechte" });
        adm.addEventListener("click", () => openRights(u));
        access.push(adm);
      }
      const hist = el("button", { type: "button", className: "ghost small", textContent: "Anmeldungen anzeigen" });
      hist.addEventListener("click", () => showHistory(u.name));
      const seen = el("span", {}, a.state === "nie" ? "noch nie aktiv" : `${STATE[a.state]} · ${ago(a.seen)}`);
      const where = [a.device, ipText(a)].filter(Boolean).join(" · ") || `Login: ${fmt(u.last_login)}`;
      const pwState = u.pending ? "selbst gewählt, wartet auf Freischaltung" : u.pw_initial ? "Startpasswort (vom Admin vergeben)"
        : u.pw_changed ? `eigenes Passwort seit ${fmt(u.pw_changed)}` : "eigenes Passwort";
      const pw = el("button", { type: "button", className: "ghost small", textContent: "🔑 Passwort neu" });
      pw.addEventListener("click", async () => {
        const own = prompt(`Neues Passwort für ${u.name} – leer lassen für ein zufälliges (mind. 10 Zeichen):`, "");
        if (own === null) return;
        try {
          const r = await api("POST", `/api/admin/users/${u.name}/password`, own ? { password: own } : {});
          showPassword("Neues Passwort", u.name, r.password, "Alte Anmeldungen dieses Logins sind beendet.");
          load().catch((e) => msg(e.message, true));
        } catch (e) { msg(e.message, true); }
      });
      const del = el("button", { type: "button", className: "ghost small", textContent: "🗑 Löschen" });
      del.addEventListener("click", () => {
        if (confirm(`Login „${u.name}“ wirklich löschen? Reisen und Pakete bleiben auf dem Server liegen.`))
          act(() => api("DELETE", `/api/admin/users/${u.name}`), `${u.name} gelöscht`);
      });
      const sect = (title, ...kids) => el("div", { className: "al-sec" }, el("h4", { textContent: title }), el("div", { className: "al-sec-body" }, ...kids));
      const body = el("div", { className: "al-body" },
        sect("Zugang", ...access),
        sect("Aktivität", seen, el("span", { className: "sub", textContent: where }),
          ...(fails ? [pill("bad", `${fails} Fehlversuch${fails > 1 ? "e" : ""}`)] : []), hist),
        sect("Passwort", el("span", { textContent: pwState }), pw),
        sect("Mails", el("span", { textContent: !u.mail ? "keine Mailadresse – bekommt keine wählbaren Mails"
          : `Neue Versionen ${nt.releases ? "✓" : "✗"} · Newsletter ${nt.newsletter ? "✓" : "✗"}${nt.asked ? "" : " · noch nicht gefragt"}` }),
          el("span", { className: "sub", textContent: "ändern nur unter Mein Konto des Nutzers" })),
        sect("Aktionen", ...(u.pending ? [] : [lock]), del));
      if (u.pending && u.note) body.prepend(el("p", { className: "al-note", textContent: `„${u.note}“` }));

      const item = el("details", { className: "al-item", open: u.name === openName }, head, body);
      item.dataset.name = u.name;
      item.dataset.find = [u.name, u.mail, u.group ? gname(u.group) : "", nt.newsletter ? "newsletter" : "", nt.releases ? "versionen" : ""]
        .join(" ").toLowerCase();   // Suche „newsletter“ zeigt alle Abonnenten
      item.addEventListener("toggle", () => {
        if (item.open) {
          openName = u.name;
          tb.querySelectorAll("details.al-item[open]").forEach((d) => { if (d !== item) d.open = false; });
        } else if (openName === u.name) openName = null;
      });
      tb.append(item);
    });
    filterLogins();
  }

  let openName = null;                                 // aufgeklappter Login – überlebt das Neuzeichnen nach Aktionen
  function filterLogins() {
    const q = ($("adm-find").value || "").trim().toLowerCase();
    let shown = 0;
    $("adm-rows").querySelectorAll(".al-item").forEach((d) => {
      d.hidden = !!q && !d.dataset.find.includes(q);
      if (!d.hidden) shown += 1;
    });
    $("adm-none").hidden = shown > 0;
  }
  $("adm-find").addEventListener("input", filterLogins);

  async function loadLog() {
    const log = await api("GET", "/api/admin/log");
    const rows = $("log-rows");
    rows.replaceChildren(...log.slice(0, 30).map((e) => el("tr", {},
      el("td", { textContent: new Date(e.t * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) }),
      el("td", { textContent: e.who }), el("td", {}, el("b", { textContent: e.target })), el("td", { textContent: e.what }))));
    if (!log.length) rows.append(el("tr", {}, el("td", { colSpan: 4, textContent: "Noch keine Änderungen protokolliert." })));
  }

  async function act(fn, ok) {
    try { await fn(); msg(ok); } catch (e) { msg(e.message, true); }
    load().catch((e) => msg(e.message, true));
    loadLog().catch(() => {});
  }

  // ---- Versionen: was laeuft + ausfuehrliches Protokoll
  const TAG = { neu: "Neu", besser: "Besser", fix: "Behoben" };
  const RUN = [["server", "Server"], ["stand", "Stand"], ["app", "RigNavi (APK)"], ["valhalla", "Valhalla"],
    ["strassen", "Straßendaten"], ["osm", "OSM-Daten"], ["regionen", "Regionen"], ["python", "Python"]];
  async function loadVersions() {
    const v = await api("GET", "/api/admin/versions");
    const run = $("ver-run");
    run.replaceChildren(...RUN.filter(([k]) => k in v.running)   // RigNavi-Zeile nur beim RigAnalyzer
      .map(([k, l]) => el("div", { className: "pill" }, el("b", { textContent: `${l}: ` }), String(v.running[k] ?? "?"))));
    const list = $("ver-list");
    list.replaceChildren();
    const KURZ = 10;   // erst die letzten 10, „Alle anzeigen“ blendet den Rest ein
    v.releases.forEach((r, i) => {
      const d = el("details", { className: "ver-item", open: i < 2 });
      const sum = el("summary", {}, el("b", { textContent: `v${r.version}` }), ` · ${r.date} · ${r.title}`);
      if (r.public) sum.append(" ", el("span", { className: "pill ok", textContent: "öffentlich" }));
      d.append(sum);
      const ul = el("ul");
      (r.items || []).forEach((it) => ul.append(el("li", {}, el("span", { className: "pill", textContent: TAG[it[0]] || it[0] }),
        " ", it[1])));
      d.append(ul);
      if (i >= KURZ) d.hidden = true;
      list.append(d);
    });
    if (v.releases.length > KURZ) {
      const more = el("button", { type: "button", className: "ghost small", id: "ver-more",
        textContent: `Alle ${v.releases.length} Versionen anzeigen` });
      more.addEventListener("click", () => {
        list.querySelectorAll("details.ver-item[hidden]").forEach((d) => { d.hidden = false; });
        more.remove();
      });
      list.append(more);
    }
  }
  loadVersions().catch((e) => msg(e.message, true));

  $("adm-new").addEventListener("click", () => $("adm-dlg").showModal());
  const grpNew = $("grp-new");   // Gruppen-Kachel nur beim Oberadmin
  if (grpNew) grpNew.addEventListener("click", () => {
    const v = prompt("Name der neuen Gruppe", "");
    if (v) act(() => api("POST", "/api/admin/groups", { name: v }), `Gruppe „${v}“ angelegt`);
  });
  $("adm-rights-cancel").addEventListener("click", () => $("adm-rights").close());
  $("adm-rights-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    const sub = { apps: f.elements[HOST_APP].checked ? [HOST_APP] : [],
      groups: [...$("adm-rights-groups").querySelectorAll("input:checked")].map((c) => c.value) };
    const keepOther = rightsUser && rightsUser.admin && rightsUser.admin !== true && rightsUser.admin.apps.includes(OTHER);
    const admin = f.elements.ober.checked ? true : sub.apps.length || keepOther ? sub : false;   // Recht der anderen App bleibt
    $("adm-rights").close();
    act(() => api("PATCH", `/api/admin/users/${rightsFor}`, { admin }), `Rechte von ${rightsFor}: ${rightsText(admin)}`);
  });
  $("adm-cancel").addEventListener("click", () => $("adm-dlg").close());
  $("adm-pw-close").addEventListener("click", () => $("adm-pw").close());
  $("adm-hist-close").addEventListener("click", () => $("adm-hist").close());
  setInterval(() => { if (!document.hidden) loadActivity(); }, 15000);   // „Jetzt aktiv“ alle 15 s
  $("adm-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const apps = [HOST_APP];
    try {
      const r = await api("POST", "/api/admin/users", { name: f.elements.uname.value, mail: f.elements.mail.value, apps,
        group: f.elements.group.value || null, send_mail: f.send.checked });
      $("adm-dlg").close();
      f.reset();
      const extra = r.mail_ok === true ? "Die Zugangsdaten sind per Mail unterwegs."
        : r.mail_ok === false ? "⚠ Mail fehlgeschlagen – Passwort bitte selbst weitergeben." : "Nur jetzt sichtbar – bitte weitergeben.";
      showPassword("Neuer Login", r.name, r.password, extra);
      load();
    } catch (err) { msg(err.message, true); }
  });

  load().catch((e) => msg(e.message, true));
  loadLog().catch(() => {});
})();
