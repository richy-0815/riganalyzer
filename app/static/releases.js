/* Hell/Dunkel-Schalter der Release-Seite (gleiche Ablage wie die App: ra.theme) */
(function () {
  var b = document.getElementById("theme-toggle");
  function sync() {
    var light = document.documentElement.getAttribute("data-theme") === "light";
    b.querySelector(".ico").textContent = light ? "🌙" : "☀️";
    b.querySelector(".lbl").textContent = light ? "Dunkel" : "Hell";
  }
  b.addEventListener("click", function () {
    var next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("ra.theme", next); } catch { /* privat */ }
    sync();
  });
  sync();
})();
