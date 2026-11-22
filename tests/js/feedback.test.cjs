// node --test tests/js/  –  Rückmeldung: Herkunftsseite aus ?von= (Proxy: Referrer-Policy no-referrer)
const test = require("node:test");
const assert = require("node:assert");
const F = require("../../app/static/feedback.js");
const O = "https://riganalyzer.petrick.top";

test("von= gewinnt, auch ohne Referrer", () => {
  assert.equal(F.fromPage("?von=%2Fkonto", "", O), "/konto");
  assert.equal(F.fromPage("?von=%2F%23planer", "", O), "/#planer");
});
test("ohne von= weiter der Referrer derselben Seite (lokal ohne Traefik)", () => {
  assert.equal(F.fromPage("", O + "/features.html", O), "/features.html");
  assert.equal(F.fromPage("", "https://evil.example/x", O), "");
});
test("nur eigene Pfade: keine fremden Adressen, kein //, nicht die Rückmeldeseite selbst", () => {
  for (const v of ["https%3A%2F%2Fevil.example%2F", "%2F%2Fevil.example", "javascript%3Aalert(1)", "konto", "%2Frueckmeldung"])
    assert.equal(F.fromPage("?von=" + v, "", O), "", v);
});
test("lang wird gekappt", () => {
  assert.equal(F.fromPage("?von=%2F" + "a".repeat(500), "", O).length, 200);
});
