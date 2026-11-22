/* Mein Konto: eigenes Passwort und eigene Mailadresse aendern; Einstellungen am Login;
   beides eingeklappt (<details>), #passwort klappt das Passwort auf. */
(function () {
  const $ = (id) => document.getElementById(id);
  const csrf = () => (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";

  async function post(url, body, method = "POST") {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf() },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `Fehler ${r.status}`);
    return j;
  }

  function msg(id, text, bad) {
    const m = $(id);
    m.textContent = text;
    m.className = bad ? "lg-err" : "lg-ok";
    m.hidden = !text;
  }

  fetch("/api/me").then((r) => r.json()).then((m) => {
    $("kt-user").textContent = m.user;
    $("kt-mail").value = m.mail || "";
    $("kt-mail-sum").textContent = m.mail ? `· ${m.mail}` : "· keine hinterlegt";
    current = m.mail || "";
    showPending(m.mail_pending);
    if ($("kt-speedwarn")) $("kt-speedwarn").checked = !m.settings || m.settings.speed_warn !== false;
  });

  if ($("kt-speedwarn")) $("kt-speedwarn").addEventListener("change", async (e) => {   // nur RigAnalyzer
    try {
      const r = await post("/api/me/settings", { speed_warn: e.target.checked });
      msg("kt-set-msg", r.settings.speed_warn ? "Tempo-Warnung an." : "Tempo-Warnung aus.");
    } catch (err) { e.target.checked = !e.target.checked; msg("kt-set-msg", err.message, true); }
  });

  if (location.hash === "#passwort") $("kt-pw").open = true;   // Direktlink aufs Passwortändern

  $("kt-pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.new1.value !== f.new2.value) return msg("kt-pw-msg", "Die beiden neuen Passwörter sind verschieden.", true);
    try {
      await post("/api/me/password", { old: f.old.value, new: f.new1.value });
      f.reset();
      msg("kt-pw-msg", "Neues Passwort gespeichert. Andere Geräte musst du neu anmelden.");
    } catch (err) { msg("kt-pw-msg", err.message, true); }
  });

  // Mailwechsel: Passwort + Bestätigungslink an die neue Adresse; bis zum Klick gilt die alte weiter
  let current = "", pendingMail = "";
  function showPending(p) {
    pendingMail = p ? p.mail : "";
    $("kt-mail-pending").hidden = !p;
    if (!p) return;
    const until = new Date(p.expires * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    $("kt-mail-pending-text").textContent = `Link an ${p.mail} geschickt, gültig bis ${until} Uhr. Bis dahin gilt `
      + (current ? `weiter ${current}.` : "noch keine Adresse.");
  }

  $("kt-mail-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await post("/api/me/mail", { mail: $("kt-mail").value, password: $("kt-mail-pw").value });
      $("kt-mail-pw").value = "";
      if (r.status === "sent") {
        showPending({ mail: r.pending, expires: r.expires });
        $("kt-mail").value = current;
        msg("kt-mail-msg", "");
      } else if (r.status === "removed") {
        current = "";
        $("kt-mail-sum").textContent = "· keine hinterlegt";
        showPending(null);
        msg("kt-mail-msg", "Mailadresse entfernt.");
      } else {
        msg("kt-mail-msg", "Das ist schon deine Adresse.");
      }
    } catch (err) { msg("kt-mail-msg", err.message, true); }
  });

  $("kt-mail-again").addEventListener("click", () => {   // Passwort ist Pflicht – nur vorbereiten
    $("kt-mail").value = pendingMail;
    $("kt-mail-pw").focus();
    msg("kt-mail-msg", "Passwort eingeben und nochmal „Bestätigungslink schicken“.");
  });

  $("kt-mail-cancel").addEventListener("click", async () => {
    try {
      await post("/api/me/mail", undefined, "DELETE");
      showPending(null);
      msg("kt-mail-msg", "Mailwechsel abgebrochen.");
    } catch (err) { msg("kt-mail-msg", err.message, true); }
  });

  if ($("kt-logout-all")) $("kt-logout-all").addEventListener("click", async () => {
    if (!confirm("Wirklich auf allen Geräten abmelden – auch hier?")) return;
    try {
      await post("/api/me/logout-all", {});
      location.href = "/login?bye=1";
    } catch (err) { msg("kt-sess-msg", err.message, true); }
  });
})();
