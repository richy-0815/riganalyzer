/* Theme vor dem ersten Zeichnen setzen (kein Aufblitzen): Standard dunkel,
   Wahl des Nutzers bleibt im Browser gespeichert. */
(function () {
  var t = "dark";
  try { t = localStorage.getItem("ra.theme") || "dark"; } catch { /* privat */ }
  document.documentElement.setAttribute("data-theme", t === "light" ? "light" : "dark");
})();

/* CSRF: jede aendernde Anfrage an die eigene Seite bekommt das Double-Submit-Token aus dem Cookie ra_csrf
   als Header X-RA-CSRF – der Server prueft das zentral. Liegt in theme.js, weil das auf jeder Seite
   als Erstes geladen wird. */
(function () {
  if (!window.fetch || window.fetch.raCsrf) return;
  var orig = window.fetch;
  var wrapped = function (input, init) {
    var method, url, tok, h;
    try {
      method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
      url = new URL(typeof input === "string" || input instanceof URL ? String(input) : input.url, location.href);
      if (method !== "GET" && method !== "HEAD" && url.origin === location.origin) {
        tok = (document.cookie.match(/(?:^|; )ra_csrf=([^;]+)/) || [])[1] || "";
        h = new Headers((init && init.headers) || (input && input.headers) || undefined);
        if (tok && !h.has("X-RA-CSRF")) {
          h.set("X-RA-CSRF", tok);
          init = Object.assign({}, init || {}, { headers: h });
        }
      }
    } catch { /* im Zweifel unveraendert senden – der Server antwortet dann 403 */ }
    return orig.call(window, input, init);
  };
  wrapped.raCsrf = true;
  window.fetch = wrapped;
})();
