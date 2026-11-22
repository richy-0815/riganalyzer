/* Newsletter-Editor: /admin/newsletter, nur Oberadmin.
   Markdown-light mit Knöpfen, Vorschau vom Server (gleiche Darstellung wie verschickt), Entwurf am Server (automatisch),
   Testmail an sich selbst, Versand an alle mit Häkchen „Newsletter“, frühere als Vorlage laden. */
(function () {
  const $ = (id) => document.getElementById(id);
  if (!$("nl-form")) return;
  const csrf = () => (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const subj = $("nl-subject"), txt = $("nl-text");
  let rcpt = 0, log = [], tPrev = 0, tDraft = 0;

  async function api(method, url, body) {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf() },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `Fehler ${r.status}`);
    return j;
  }
  function msg(text, bad) {
    const m = $("nl-msg");
    m.textContent = text;
    m.className = bad ? "lg-err" : "lg-ok";
    m.hidden = !text;
  }

  // ---- Vorschau (Server rendert; das HTML ist dort maskiert, eigenes HTML im Text gibt es nicht)
  async function preview() {
    try {
      const j = await api("POST", "/api/admin/newsletter/preview", { subject: subj.value, text: txt.value });
      rcpt = j.recipients;
      $("nl-rcpt").textContent = `${rcpt} Empfänger`;
      $("nl-send").textContent = `An ${rcpt} Empfänger schicken`;
      $("nl-preview").innerHTML = `<div class="nl-subj"><small>Betreff</small> ${esc(subj.value || "(ohne Betreff)")}</div>` + j.html;
    } catch (e) { $("nl-preview").textContent = e.message; }
  }
  // ---- Entwurf am Server, 1,5 s nach der letzten Änderung
  async function saveDraft() {
    try {
      await api("PUT", "/api/admin/newsletter/draft", { subject: subj.value, text: txt.value });
      $("nl-draft-state").textContent = `Entwurf gespeichert ${new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}.`;
    } catch (e) { $("nl-draft-state").textContent = `Entwurf nicht gespeichert: ${e.message}`; }
  }
  function changed() {
    clearTimeout(tPrev); clearTimeout(tDraft);
    tPrev = setTimeout(preview, 350);
    tDraft = setTimeout(saveDraft, 1500);
  }
  subj.addEventListener("input", changed);
  txt.addEventListener("input", changed);

  // ---- Knöpfe: Markdown um die Auswahl bzw. am Zeilenanfang
  function wrap(before, after, ph) {
    const a = txt.selectionStart, b = txt.selectionEnd, sel = txt.value.slice(a, b) || ph;
    txt.setRangeText(before + sel + after, a, b, "end");
    if (sel === ph) txt.setSelectionRange(a + before.length, a + before.length + ph.length);
  }
  function linePrefix(p) {
    const a = txt.selectionStart, start = txt.value.lastIndexOf("\n", a - 1) + 1;
    const end = txt.value.indexOf("\n", txt.selectionEnd);
    const block = txt.value.slice(start, end < 0 ? txt.value.length : end);
    txt.setRangeText(block.split("\n").map((l) => (l.startsWith(p) ? l : p + l.replace(/^(#+ |- )/, ""))).join("\n"),
      start, end < 0 ? txt.value.length : end, "end");
  }
  document.querySelectorAll("[data-md]").forEach((btn) => btn.addEventListener("click", () => {
    txt.focus();
    const k = btn.dataset.md;
    if (k === "b") wrap("**", "**", "fett");
    else if (k === "i") wrap("*", "*", "kursiv");
    else if (k === "h1") linePrefix("# ");
    else if (k === "h2") linePrefix("## ");
    else if (k === "ul") linePrefix("- ");
    else if (k === "a") {
      const url = prompt("Adresse des Links (https://…)", "https://");
      if (!url || !/^https?:\/\/\S+$/.test(url)) return;
      wrap("[", `](${url})`, "Linktext");
    }
    changed();
  }));

  // ---- Verschicken
  async function send(test) {
    if (!$("nl-form").reportValidity()) return;
    if (!test && !confirm(`Newsletter „${subj.value}“ jetzt an ${rcpt} Empfänger schicken?`)) return;
    msg(test ? "Testmail geht raus …" : "Wird verschickt …");
    try {
      const j = await api("POST", "/api/admin/newsletter", { subject: subj.value, text: txt.value, test });
      if (test) msg(j.sent ? "Testmail ist raus – schau ins Postfach." : "Testmail nicht angenommen (in QA: QA-Postfach).");
      else {
        msg(`Verschickt: ${j.sent} von ${j.total}.`);
        subj.value = ""; txt.value = "";
        preview(); loadLog();
      }
    } catch (e) { msg(e.message, true); }
  }
  $("nl-test").addEventListener("click", () => send(true));
  $("nl-form").addEventListener("submit", (e) => { e.preventDefault(); send(false); });
  $("nl-clear").addEventListener("click", () => {
    if ((subj.value || txt.value) && !confirm("Entwurf leeren?")) return;
    subj.value = ""; txt.value = ""; changed();
  });

  // ---- Bisher verschickt, als Vorlage laden
  async function loadLog() {
    const j = await api("GET", "/api/admin/newsletter");
    log = j.log;
    $("nl-log").innerHTML = log.length ? log.map((x, i) => `<li>${esc(new Date(x.at * 1000).toLocaleString("de-DE"))} · `
      + `<b>${esc(x.subject)}</b> · ${x.sent}/${x.total} · ${esc(x.by)}`
      + `${x.text ? ` <button type="button" class="linkish" data-tpl="${i}">als Vorlage</button>` : ""}</li>`).join("")
      : '<li class="hint">noch keiner</li>';
  }
  $("nl-log").addEventListener("click", (e) => {
    const i = e.target.dataset && e.target.dataset.tpl;
    if (i === undefined) return;
    if ((subj.value || txt.value) && !confirm("Aktuellen Entwurf durch die Vorlage ersetzen?")) return;
    subj.value = log[i].subject; txt.value = log[i].text; changed();
  });

  api("GET", "/api/admin/newsletter/draft").then((d) => {
    subj.value = d.subject || ""; txt.value = d.text || "";
    if (d.at) $("nl-draft-state").textContent = `Entwurf von ${new Date(d.at * 1000).toLocaleString("de-DE")} geladen.`;
    preview();
  }).catch((e) => msg(e.message, true));
  loadLog().catch(() => {});
})();
