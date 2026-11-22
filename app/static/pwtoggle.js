/* Auge an jedem Passwortfeld: Passwort kurz sichtbar machen, um Tippfehler zu sehen */
(function () {
  document.querySelectorAll('input[type="password"]').forEach(function (inp) {
    var wrap = document.createElement("span");
    wrap.className = "pw-wrap";
    inp.parentNode.insertBefore(wrap, inp);
    wrap.appendChild(inp);
    var b = document.createElement("button");
    b.type = "button";
    b.className = "pw-eye";
    b.textContent = "👁";
    function sync() {
      var shown = inp.type === "text";
      b.setAttribute("aria-label", shown ? "Passwort verbergen" : "Passwort anzeigen");
      b.title = shown ? "Passwort verbergen" : "Passwort anzeigen";
      b.setAttribute("aria-pressed", shown ? "true" : "false");
    }
    b.addEventListener("click", function () {
      inp.type = inp.type === "password" ? "text" : "password";
      sync();
      inp.focus();
    });
    sync();
    wrap.appendChild(b);
  });
  // vor dem Absenden wieder verbergen, damit der Browser es als Passwort speichert
  document.querySelectorAll("form").forEach(function (f) {
    f.addEventListener("submit", function () {
      f.querySelectorAll(".pw-wrap input").forEach(function (i) { i.type = "password"; });
    });
  });
})();
