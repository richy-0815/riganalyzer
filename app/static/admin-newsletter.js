/* Admin – Kachel „Newsletter“ (nur Oberadmin): wie viele wollen was, bisher verschickte.
   Schreiben und Verschicken auf der eigenen Seite /admin/newsletter (newsletter-editor.js). */
(function () {
  const $ = (id) => document.getElementById(id);
  if (!$("nl-count")) return;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  fetch("/api/admin/newsletter").then((r) => (r.ok ? r.json() : Promise.reject(r))).then((j) => {
    $("nl-count").textContent = `${j.newsletter} wollen den Newsletter, ${j.releases} die Sammelmail „Neue Versionen“.`;
    $("nl-log").innerHTML = j.log.length ? j.log.map((x) => `<li>${esc(new Date(x.at * 1000).toLocaleString("de-DE"))} · `
      + `${esc(x.subject)} · ${x.sent}/${x.total} · ${esc(x.by)}</li>`).join("") : '<li class="hint">noch keiner</li>';
  }).catch(() => { $("nl-count").textContent = "Nicht verfügbar."; });
})();
