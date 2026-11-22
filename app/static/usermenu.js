/* Login-Menue oben rechts: ersetzt den Abmelde-Knopf durch Name + Menue
   (Mein Konto, Admin – nur fuer Admins –, Abmelden). Funktioniert auf allen Seiten mit .logout-btn. */
(function () {
  const old = document.querySelector(".logout-btn");
  if (!old) return;
  fetch("/api/me").then((r) => (r.ok ? r.json() : null)).then((m) => {
    if (!m || !m.user) return;
    const wrap = document.createElement("div");
    wrap.className = "usermenu";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "theme-toggle";
    btn.setAttribute("aria-haspopup", "menu");
    btn.setAttribute("aria-expanded", "false");
    const av = document.createElement("span");
    av.className = "avatar";
    av.textContent = m.user.slice(0, 1);
    const lbl = document.createElement("span");
    lbl.className = "lbl";
    lbl.textContent = m.user;
    btn.append(av, lbl);
    const ul = document.createElement("ul");
    ul.setAttribute("role", "menu");
    ul.hidden = true;
    const item = (href, text) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = href; a.textContent = text; a.setAttribute("role", "menuitem");
      li.appendChild(a); return li;
    };
    const who = document.createElement("li");
    const s = document.createElement("span");
    s.textContent = `Angemeldet als ${m.user}${m.admin === true ? " · Admin" : m.admin ? " · Gruppen-Admin" : ""}`;
    who.appendChild(s);
    ul.append(who, item("/konto", "👤 Mein Konto"), item("/rueckmeldung?von=" + encodeURIComponent(location.pathname + location.hash), "💬 Rückmeldung"));
    if (m.admin) ul.appendChild(item("/admin", "🛠 Admin – Logins"));
    ul.appendChild(item("/logout", "⎋ Abmelden"));
    wrap.append(btn, ul);
    old.replaceWith(wrap);
    const close = () => { ul.hidden = true; btn.setAttribute("aria-expanded", "false"); };
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      ul.hidden = !ul.hidden;
      btn.setAttribute("aria-expanded", String(!ul.hidden));
    });
    document.addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }).catch(() => {});
})();
