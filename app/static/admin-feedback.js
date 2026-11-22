/* Admin – Rueckmeldungen: nur Oberadmin, je App die eigenen; 404 = Karte bleibt versteckt.
   „Als Ticket vormerken“ – das Issue legt der Rueckmeldungs-Abgleich an. ticketLabel per require testbar.
   erledigte loeschen – einzeln (🗑 nur bei abgehakten) und gesammelt („Erledigte löschen (N)“). */
(function (root) {
  function ticketLabel(t) {
    if (!t) return "🎫 Als Ticket vormerken";
    if (t === "vorgemerkt") return "🎫 vorgemerkt – zurücknehmen";
    return `🎫 Ticket #${t.nr} · ${t.status}`;
  }
  const bulkLabel = (n) => `🗑 Erledigte löschen (${n})`;
  const when = (iso) => new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
  function confirmOne(x) {
    const date = new Date(x.t).toLocaleDateString("de-DE");
    let q = `Rückmeldung von ${x.user} vom ${date} endgültig löschen?`;
    if (x.ticket === "vorgemerkt") q += "\nSie ist als Ticket vorgemerkt – es wird dann keins mehr angelegt.";
    return q;
  }
  function confirmBulk(items) {
    const done = items.filter((x) => x.done);
    let q = `${done.length === 1 ? "Eine erledigte Rückmeldung" : `${done.length} erledigte Rückmeldungen`} endgültig löschen?`;
    const pending = done.filter((x) => x.ticket === "vorgemerkt").length;
    if (pending) q += `\n${pending} davon als Ticket vorgemerkt – dafür wird dann keins mehr angelegt.`;
    return q;
  }
  const api = { ticketLabel, bulkLabel, confirmOne, confirmBulk };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AdminFeedback = api;
  if (typeof document === "undefined") return;
  const card = document.getElementById("fb-card");
  if (!card) return;
  const list = document.getElementById("fb-list");
  const csrf = () => (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
  const KIND = { fehler: "🐞 Fehler", idee: "💡 Idee", lob: "👍 Lob", sonstiges: "💬 Sonstiges" };
  const el = (tag, props = {}, ...kids) => {
    const n = Object.assign(document.createElement(tag), props);
    kids.forEach((k) => n.append(k));
    return n;
  };
  const bulk = el("button", { type: "button", className: "ghost small", id: "fb-bulk", hidden: true });
  list.before(el("div", { className: "fb-bar" }, bulk));
  let shown = [];
  const refreshBulk = () => {
    const n = shown.filter((x) => x.done).length;
    bulk.hidden = !n;
    bulk.textContent = bulkLabel(n);
  };
  bulk.addEventListener("click", async () => {
    if (!window.confirm(confirmBulk(shown))) return;
    await fetch("/api/admin/feedback/erledigte-loeschen", { method: "POST", headers: { "X-RA-CSRF": csrf() } }).catch(() => null);
    load();
  });

  async function load() {
    const r = await fetch("/api/admin/feedback").catch(() => null);
    if (!r || !r.ok) { card.hidden = true; return; }
    const items = await r.json();
    shown = items;
    refreshBulk();
    list.replaceChildren();
    if (!items.length) { list.append(el("p", { className: "hint", textContent: "Noch keine Rückmeldungen." })); return; }
    for (const x of items) {
      const done = el("input", { type: "checkbox", checked: !!x.done, title: "erledigt" });
      done.addEventListener("change", async () => {
        const res = await fetch(`/api/admin/feedback/${encodeURIComponent(x.id)}`, { method: "PATCH",
          headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf() }, body: JSON.stringify({ done: done.checked }) });
        if (!res.ok) done.checked = !done.checked;
        x.done = done.checked;
        item.classList.toggle("done", done.checked);
        del.hidden = !done.checked;
        refreshBulk();
      });
      const del = el("button", { type: "button", className: "ghost small fb-del", textContent: "🗑 Löschen", hidden: !x.done });
      del.addEventListener("click", async () => {
        if (!window.confirm(confirmOne({ ...x, ticket }))) return;
        const res = await fetch(`/api/admin/feedback/${encodeURIComponent(x.id)}`, { method: "DELETE",
          headers: { "X-RA-CSRF": csrf() } }).catch(() => null);
        if (res && res.ok) {
          item.remove();
          shown = shown.filter((y) => y !== x);
          refreshBulk();
          if (!shown.length) load();
        } else load();
      });
      const head = el("div", { className: "fb-head" }, el("b", { textContent: KIND[x.kind] || x.kind }),
        ` · ${x.user} · ${when(x.t)}${x.page ? ` · ${x.page}` : ""}`);
      const tools = el("div", { className: "fb-tools" }, el("label", {}, done, " erledigt"));
      let ticket = x.ticket;
      const tk = el("button", { type: "button", className: "ghost small fb-ticket", textContent: ticketLabel(ticket) });
      tk.disabled = !!(ticket && typeof ticket === "object");   // angelegtes Ticket laesst sich hier nicht zuruecknehmen
      tk.addEventListener("click", async () => {
        const on = !ticket;
        const res = await fetch(`/api/admin/feedback/${encodeURIComponent(x.id)}`, { method: "PATCH",
          headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf() }, body: JSON.stringify({ ticket: on }) });
        if (res.ok) { ticket = on ? "vorgemerkt" : undefined; x.ticket = ticket; tk.textContent = ticketLabel(ticket); }
        else load();   // z. B. 409: inzwischen gibt es ein Ticket – Liste neu laden zeigt den Stand
      });
      tools.append(tk);
      if (x.mail) {
        tools.append(el("a", { className: "ghost small", textContent: "✉ Antworten",
          href: `mailto:${x.mail}?subject=${encodeURIComponent("Deine Rückmeldung")}` }));
      }
      tools.append(del);
      const item = el("article", { className: `fb-item${x.done ? " done" : ""}` }, head,
        el("p", { className: "fb-text", textContent: x.text }), tools);
      list.append(item);
    }
  }
  load();
})(typeof window !== "undefined" ? window : globalThis);
