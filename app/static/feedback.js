/* Rueckmeldung: Formular -> POST /api/feedback (Cookie + CSRF).
   Herkunftsseite aus ?von= (das Menue haengt sie an) – der Reverse-Proxy setzt „Referrer-Policy: no-referrer“,
   document.referrer ist im Betrieb deshalb leer. Lokal ohne Proxy bleibt der Referrer als Rueckfall.
   Reine Funktion fromPage auch fuer node --test. */
(function (root) {
  const MAX = 200;
  const ok = (p) => typeof p === "string" && p.startsWith("/") && !p.startsWith("//") && !p.startsWith("/rueckmeldung");
  function fromPage(search, referrer, origin) {
    try {
      const v = new URLSearchParams(search || "").get("von");
      if (v) return ok(v) ? v.slice(0, MAX) : "";
      const r = referrer && new URL(referrer);
      const p = r && r.origin === origin ? r.pathname + r.hash : "";
      return ok(p) ? p.slice(0, MAX) : "";
    } catch { return ""; }
  }
  if (typeof module !== "undefined" && module.exports) { module.exports = { fromPage }; return; }
  root.FeedbackForm = { fromPage };

  const $ = (id) => document.getElementById(id);
  const csrf = () => (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
  const form = $("fb-form");
  if (!form) return;
  const from = fromPage(location.search, document.referrer, location.origin);
  function show(text, bad) {
    const m = $("fb-msg");
    m.textContent = text;
    m.className = bad ? "lg-err" : "lg-ok";
    m.hidden = false;
  }
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    const body = { kind: form.kind.value, text: form.text.value, page: form.page.checked ? from : "" };
    try {
      const r = await fetch("/api/feedback", { method: "POST",
        headers: { "Content-Type": "application/json", "X-RA-CSRF": csrf() }, body: JSON.stringify(body) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `Fehler ${r.status}`);
      form.reset();
      show("Danke! Deine Rückmeldung ist angekommen.");
    } catch (e) {
      show(e.message || "Senden nicht möglich.", true);
    } finally {
      btn.disabled = false;
    }
  });
})(typeof window !== "undefined" ? window : globalThis);
