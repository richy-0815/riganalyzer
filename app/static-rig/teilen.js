/* Touren teilen (nur RigAnalyzer): Dialog „🔗 Teilen“ für „Meine Touren“ und den Planer.
   Nur Logins, fester Stand, der Empfänger muss annehmen. Reine Funktionen (stateText, inboxLine, sharedWith) auch für
   node --test; im Browser window.Teilen. Alle Texte per textContent (Tour-Namen kommen vom Nutzer). */
(function (root) {
  const STATE = { offen: "offen", angenommen: "✓ angenommen", abgelehnt: "abgelehnt", zurückgezogen: "zurückgezogen", abgelaufen: "abgelaufen" };
  const stateText = (s) => STATE[s] || s;
  const two = (n) => String(n).padStart(2, "0");
  const dm = (iso) => { const t = new Date(iso); return `${two(t.getDate())}.${two(t.getMonth() + 1)}.`; };
  const inboxLine = (x) => `„${x.name}“ von ${x.from} · ${x.stages} Etappe${x.stages === 1 ? "" : "n"} · ${dm(x.created)}`;
  function sharedWith(outbox, tripId) {
    const mine = (outbox || []).filter((x) => x.trip_id === tripId);
    return mine.length ? `geteilt mit ${mine.map((x) => `${x.to} (${stateText(x.state)})`).join(", ")}` : "";
  }
  const api = { stateText, inboxLine, sharedWith };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  const csrf = () => (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
  const el = (tag, props = {}, ...kids) => {
    const n = Object.assign(document.createElement(tag), props);
    kids.forEach((k) => n.append(k));
    return n;
  };
  let dlg = null;
  /** Dialog öffnen: Login-Name eintippen, Server-Antwort anzeigen; onDone nach erfolgreichem Teilen */
  function open(tripId, tripName, onDone) {
    if (dlg) dlg.remove();
    const input = el("input", { name: "to", required: true, autocomplete: "off", autocapitalize: "none", spellcheck: false, maxLength: 64,
      placeholder: "Login-Name der anderen Person" });
    const msg = el("p", { className: "hint", hidden: true });
    const send = el("button", { type: "submit", className: "primary", textContent: "Teilen" });
    const close = el("button", { type: "button", className: "ghost", textContent: "Schließen" });
    const form = el("form", { className: "lg-form", method: "dialog" },
      el("h3", { textContent: `🔗 „${tripName || "Tour"}“ teilen`, style: "margin-top:0" }),
      el("p", { className: "hint", textContent: "Der andere Login bekommt den jetzigen Stand als eigene Kopie, sobald er annimmt. "
        + "Spätere Änderungen von dir kommen nicht an. Deine Notiz zur Tour bleibt privat." }),
      // Beschriftung über dem Feld, darunter der Hinweis zum Abschicken
      el("label", {}, "Login-Name", input, el("small", { textContent: "Enter oder „Teilen“ schickt ab." })),
      el("div", { className: "dlg-btns" }, send, close), msg);
    dlg = el("dialog", { className: "dlg teilen-dlg" }, form);
    close.addEventListener("click", () => dlg.close());
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      send.disabled = true;
      const r = await fetch("/api/shares", { method: "POST", headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf() },
        body: JSON.stringify({ trip_id: tripId, to: input.value }) }).catch(() => null);
      const j = r ? await r.json().catch(() => ({})) : {};
      msg.hidden = false;
      msg.className = r && r.ok ? "lg-ok" : "lg-err";
      msg.textContent = r && r.ok ? j.text : j.detail || "Teilen nicht möglich – bitte später noch einmal.";
      send.disabled = false;
      if (r && r.ok) { input.value = ""; if (onDone) onDone(); }
    });
    document.body.append(dlg);
    dlg.showModal();
    input.focus();
  }
  root.Teilen = { ...api, open };
})(typeof window !== "undefined" ? window : globalThis);
