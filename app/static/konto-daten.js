/* Mein Konto – „Meine Daten“: Übersicht aus /api/me/data/overview; der Download ist ein normaler Link. */
(function () {
  const $ = (id) => document.getElementById(id);
  if (!$("kt-daten")) return;
  const two = (n) => String(n).padStart(2, "0");
  const when = (s) => {
    const t = new Date(s * 1000);
    return `${two(t.getDate())}.${two(t.getMonth() + 1)}. ${two(t.getHours())}:${two(t.getMinutes())}`;
  };
  const el = (tag, text) => Object.assign(document.createElement(tag), { textContent: text });

  function fill(dl, rows) {
    dl.replaceChildren();
    for (const r of rows) dl.append(el("dt", r.label), el("dd", r.value));
  }

  fetch("/api/me/data/overview").then((r) => (r.ok ? r.json() : Promise.reject(r.status))).then((v) => {
    fill($("kt-daten-stored"), v.gespeichert);
    fill($("kt-daten-month"), v.monat.werte);
    const body = $("kt-logins-body");
    body.replaceChildren();
    $("kt-logins-sum").textContent = `(${v.anmeldungen.length})`;
    if (!v.anmeldungen.length) {
      const td = el("td", "keine");
      td.colSpan = 4;
      const tr = document.createElement("tr");
      tr.append(td);
      body.append(tr);
      return;
    }
    for (const a of v.anmeldungen) {
      const tr = document.createElement("tr");
      tr.append(el("td", when(a.t)), el("td", a.device), el("td", a.ip), el("td", a.ok ? "✓" : "✗ Fehlversuch"));
      if (!a.ok) tr.className = "bad";
      body.append(tr);
    }
  }).catch(() => { $("kt-daten-stored").replaceChildren(el("dt", "konnte nicht geladen werden")); });
})();
