/* Mein Konto – Benachrichtigungen: „Neue Versionen“ (abends eine Sammelmail) und Newsletter.
   Gespeichert am Login (prefs „mail“), Standard aus. Wer noch nie gefragt wurde und eine Mailadresse hat, sieht einmal
   die Frage „Wollt ihr Mails zu Neuerungen?“ – Ja schaltet beides ein, Nein lässt es aus; danach nie wieder. */
(function () {
  const $ = (id) => document.getElementById(id);
  if (!$("kt-notify")) return;
  const csrf = () => (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
  let cur = { releases: false, newsletter: false, asked: false };

  function msg(text, bad) {
    const m = $("kt-notify-msg");
    m.textContent = text;
    m.className = bad ? "lg-err" : "lg-ok";
    m.hidden = !text;
  }

  function show(hasMail) {
    $("kt-notify-releases").checked = cur.releases;
    $("kt-notify-newsletter").checked = cur.newsletter;
    $("kt-notify-releases").disabled = $("kt-notify-newsletter").disabled = !hasMail;
    $("kt-notify-nomail").hidden = hasMail;
    $("kt-notify-ask").hidden = !hasMail || cur.asked;
  }

  async function save(next, text) {
    const body = { mail: { ...next, asked: true, at: Date.now() } };
    const r = await fetch("/api/me/prefs", { method: "PUT", headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf() },
      body: JSON.stringify(body) });
    if (!r.ok) throw new Error(((await r.json().catch(() => ({}))).detail) || `Fehler ${r.status}`);
    cur = { ...next, asked: true };
    $("kt-notify").classList.remove("kt-ask-on");
    show(true);
    msg(text);
  }

  fetch("/api/me").then((r) => r.json()).then((m) => {
    cur = { ...cur, ...(m.notify || {}) };
    show(!!m.mail);
    if (m.mail && !cur.asked) $("kt-notify").classList.add("kt-ask-on");
  });

  for (const k of ["releases", "newsletter"]) {
    $(`kt-notify-${k}`).addEventListener("change", async (e) => {
      try {
        await save({ ...cur, [k]: e.target.checked }, `${k === "releases" ? "Neue Versionen" : "Newsletter"} ${e.target.checked ? "an" : "aus"}.`);
      } catch (err) { e.target.checked = !e.target.checked; msg(err.message, true); }
    });
  }
  $("kt-notify-yes").addEventListener("click", () => save({ releases: true, newsletter: true }, "Danke! Ihr bekommt Neuerungen per Mail.")
    .catch((err) => msg(err.message, true)));
  $("kt-notify-no").addEventListener("click", () => save({ releases: false, newsletter: false }, "Alles klar – keine Mails. Ändern geht hier jederzeit.")
    .catch((err) => msg(err.message, true)));
})();
