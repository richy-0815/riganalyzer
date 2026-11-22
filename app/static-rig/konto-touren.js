/* Mein Konto – „Meine Touren“ (nur RigAnalyzer): gespeicherte Touren mit Navi-Paket-Stand.
   Reine Funktionen (pkgLine, hasPackage, summary, meta) auch fuer node --test; im Browser window.KontoTouren. */
(function (root) {
  const de = (x, d = 1) => x.toLocaleString("de-DE", { maximumFractionDigits: d });
  const two = (n) => String(n).padStart(2, "0");
  const stamp = (iso) => {
    const t = new Date(iso);
    return `${two(t.getDate())}.${two(t.getMonth() + 1)}. ${two(t.getHours())}:${two(t.getMinutes())}`;
  };
  const day = (iso) => {
    const t = new Date(iso);
    return `${two(t.getDate())}.${two(t.getMonth() + 1)}.${String(t.getFullYear()).slice(2)}`;
  };

  function pkgLine(p) {
    if (!p || !p.state || p.state === "none") return "kein Navi-Paket";
    if (p.state === "ready") return `📱 Navi-Paket bereit · ${de((p.size || 0) / 1e9)} GB${p.built ? " · Stand " + stamp(p.built) : ""}`;
    if (p.state === "building") return `📱 Navi-Paket wird gebaut · ${p.progress || 0} %`;
    if (p.state === "queued") return "📱 Navi-Paket wartet auf den Bau";
    if (p.state === "error") return `⚠ Navi-Paket fehlgeschlagen: ${p.msg || "unbekannt"}`;
    return "kein Navi-Paket";
  }
  const hasPackage = (t) => !!(t.package && t.package.state === "ready");
  function summary(list) {
    if (!list.length) return "noch keine Touren";
    const n = `${list.length} Tour${list.length === 1 ? "" : "en"}`;
    const gb = list.filter(hasPackage).reduce((s, t) => s + (t.package.size || 0), 0) / 1e9;
    return gb > 0 ? `${n} · Navi-Pakete ${de(gb)} GB` : `${n} · keine Navi-Pakete`;
  }
  const meta = (t) => `${t.from || ""} → ${t.to || ""} · ${t.stages} Etappe${t.stages === 1 ? "" : "n"}`
    + (t.updated ? ` · ${day(t.updated)}` : "");

  const api = { pkgLine, hasPackage, summary, meta };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.KontoTouren = api;

  /* ---------------------------------------------------------------- Seite */
  const $ = (id) => document.getElementById(id);
  const box = $("kt-touren");
  if (!box) return;
  const csrf = () => (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
  const send = (method, url, body) => fetch(url, { method,
    headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf() }, body: body ? JSON.stringify(body) : undefined });
  const el = (tag, props = {}, ...kids) => {
    const n = Object.assign(document.createElement(tag), props);
    kids.forEach((k) => n.append(k));
    return n;
  };
  const btn = (text, title, onClick) => {
    const b = el("button", { type: "button", className: "ghost small", textContent: text, title });
    b.addEventListener("click", onClick);
    return b;
  };
  function note(text, bad) {
    const m = $("kt-touren-msg");
    m.textContent = text; m.className = bad ? "lg-err" : "lg-ok"; m.hidden = !text;
  }

  // „Für dich geteilt“ – Eingang über der Liste (nur wenn es offene Einladungen gibt)
  const inboxBox = el("div", { className: "kt-geteilt", hidden: true });
  $("kt-touren-liste").before(inboxBox);
  function renderInbox(inbox) {
    inboxBox.replaceChildren();
    inboxBox.hidden = !inbox.length;
    if (!inbox.length) return;
    inboxBox.append(el("h5", { textContent: "📨 Für dich geteilt" }));
    for (const x of inbox) {
      inboxBox.append(el("div", { className: "kt-tour" }, el("span", { textContent: Teilen.inboxLine(x) }), " ",
        btn("Annehmen", "Als eigene Tour übernehmen", async () => {
          const r = await send("POST", `/api/shares/${encodeURIComponent(x.id)}/accept`);
          note(r.ok ? `„${x.name}“ steht jetzt in deinen Touren.` : "Annehmen nicht möglich.", !r.ok); load();
        }),
        btn("Ablehnen", "Einladung ablehnen", async () => {
          if (!confirm(`Tour „${x.name}“ von ${x.from} ablehnen?`)) return;
          const r = await send("POST", `/api/shares/${encodeURIComponent(x.id)}/decline`);
          note(r.ok ? "Abgelehnt." : "Ablehnen nicht möglich.", !r.ok); load();
        })));
    }
  }

  async function load() {
    clearInterval(liveTimer); live.clear();
    const [list, sh] = await Promise.all([
      fetch("/api/trips").then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch("/api/shares").then((r) => (r.ok ? r.json() : null)).catch(() => null)]);
    const outbox = (sh && sh.outbox) || [];
    renderInbox((sh && sh.inbox) || []);
    const ul = $("kt-touren-liste");
    ul.replaceChildren();
    if (!list) { $("kt-touren-sum").textContent = "konnte nicht geladen werden"; return; }
    $("kt-touren-sum").textContent = summary(list);
    if (!list.length) {
      ul.append(el("li", { className: "hint", textContent: "Im Routenplaner planen und „💾 Speichern“ – dann steht die Tour hier." }));
      return;
    }
    for (const t of list) {
      const vers = el("div", { className: "kt-vers", hidden: true });
      const tools = el("div", { className: "kt-tools" },
        btn("Im Planer öffnen", "Tour im Routenplaner laden", () => { location.href = `/?tour=${encodeURIComponent(t.id)}`; }),
        btn("Kopie", "Als neue Tour kopieren", async () => {
          const r = await send("POST", `/api/trips/${t.id}/copy`);
          note(r.ok ? `„${t.name}“ kopiert.` : "Kopieren nicht möglich.", !r.ok); load();
        }),
        btn("🕘 Stände", "Frühere Stände", async () => {
          vers.hidden = !vers.hidden;
          if (vers.hidden) return;
          const j = await fetch(`/api/trips/${t.id}/versions`).then((r) => r.json()).catch(() => ({ versions: [] }));
          vers.replaceChildren();
          if (!j.versions.length) { vers.append(el("p", { className: "hint", textContent: "Keine früheren Stände." })); return; }
          for (const v of j.versions) {
            vers.append(el("div", {}, `${day(v.updated)} · ${v.name || ""} · ${v.stops} Ziele `,
              btn("wiederherstellen", "Diesen Stand wiederherstellen", async () => {
                if (!confirm("Diesen Stand wiederherstellen? Der jetzige wird als früherer Stand aufgehoben.")) return;
                const r = await send("POST", `/api/trips/${t.id}/restore`, { v: v.v });
                note(r.ok ? "Stand wiederhergestellt." : "Wiederherstellen nicht möglich.", !r.ok); load();
              })));
          }
        }));
      if (hasPackage(t)) {
        tools.append(btn("Paket löschen", "Nur das Navi-Paket vom Server löschen – die Tour bleibt", async () => {
          if (!confirm("Navi-Paket vom Server löschen? Die Tour bleibt; aufs Handy schon geladene Pakete bleiben dort.")) return;
          const r = await send("DELETE", `/api/trips/${t.id}/package`);
          const j = r.ok ? {} : await r.json().catch(() => ({}));
          note(r.ok ? "Navi-Paket gelöscht." : j.detail || "Löschen nicht möglich.", !r.ok); load();
        }));
      }
      tools.append(btn("🔗 Teilen", "Mit einem anderen Login teilen", () => Teilen.open(t.id, t.name, load)));
      const shared = el("div", { className: "kt-shared" });
      const sw = Teilen.sharedWith(outbox, t.id);
      if (sw) {
        shared.append(sw);
        for (const x of outbox.filter((y) => y.trip_id === t.id && y.state === "offen")) {
          shared.append(" ", btn(`${x.to} zurückziehen`, "Offene Einladung zurückziehen", async () => {
            const r = await send("DELETE", `/api/shares/${encodeURIComponent(x.id)}`);
            note(r.ok ? "Einladung zurückgezogen." : "Zurückziehen nicht möglich.", !r.ok); load();
          }));
        }
      } else shared.hidden = true;
      tools.append(btn("🗑", "Tour löschen", async () => {
        if (!confirm(`Tour „${t.name}“ löschen? Ein Navi-Paket dieser Tour wird ebenfalls gelöscht.`)) return;
        const r = await send("DELETE", `/api/trips/${t.id}`);
        note(r.ok ? `„${t.name}“ gelöscht.` : "Löschen nicht möglich.", !r.ok); load();
      }));
      const pkg = el("div", { className: "kt-pkg", textContent: pkgLine(t.package) });
      showPkg(pkg, t);
      if (busy(t.package)) live.set(t.id, { node: pkg, t });
      ul.append(el("li", { className: "kt-tour" },
        el("div", { className: "kt-head" }, el("b", { textContent: t.name }), el("small", { textContent: meta(t) })),
        pkg, shared, tools, vers));
    }
    if (live.size) liveTimer = setInterval(poll, 5000);
  }

  // Paket-Stand als Balken + Schritte (navistatus.js); laufende Bauten alle 5 s nachfragen
  const busy = (p) => !!(p && (p.state === "queued" || p.state === "building"));
  const live = new Map();
  let liveTimer = null;
  function showPkg(node, t) {
    if (!root.NaviStatus) { node.textContent = pkgLine(t.package); return; }
    root.NaviStatus.render(node, t.package, { onRetry: async () => {
      const r = await send("POST", `/api/trips/${t.id}/package`);
      const j = r.ok ? {} : await r.json().catch(() => ({}));
      note(r.ok ? `Navi-Paket für „${t.name}“ wird neu gebaut.` : j.detail || "Neu bauen nicht möglich.", !r.ok); load();
    } });
  }
  async function poll() {
    let done = false;
    for (const [id, x] of live) {
      const s = await fetch(`/api/trips/${encodeURIComponent(id)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
      if (!s) continue;
      x.t.package = s.package;
      showPkg(x.node, x.t);
      if (!busy(s.package)) done = true;
    }
    if (done) load();   // fertig/Fehler: Liste neu (Summe, „Paket löschen“)
  }
  load();
})(typeof window !== "undefined" ? window : globalThis);
