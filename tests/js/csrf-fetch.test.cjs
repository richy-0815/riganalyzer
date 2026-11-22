// node --test tests/js/  –  theme.js haengt X-RA-CSRF an aendernde fetch()-Aufrufe der eigenen Seite
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function load() {
  const calls = [];
  const win = {
    fetch: (input, init) => { calls.push({ input, init }); return Promise.resolve("ok"); },
  };
  const ctx = {
    window: win, location: { href: "https://riganalyzer.petrick.top/konto", origin: "https://riganalyzer.petrick.top" },
    document: { cookie: "x=1; ra_csrf=TOK123", documentElement: { setAttribute() {} } },
    localStorage: { getItem: () => null }, URL, Headers, Object,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../app/static/theme.js"), "utf8"), ctx);
  return { win, calls };
}

test("POST an die eigene Seite bekommt das Token", async () => {
  const { win, calls } = load();
  await win.fetch("/api/trips", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  const h = calls[0].init.headers;
  assert.strictEqual(h.get("X-RA-CSRF"), "TOK123");
  assert.strictEqual(h.get("Content-Type"), "application/json");
});

test("DELETE ohne init.headers bekommt das Token", async () => {
  const { win, calls } = load();
  await win.fetch("/api/trips/abc", { method: "DELETE" });
  assert.strictEqual(calls[0].init.headers.get("X-RA-CSRF"), "TOK123");
});

test("GET bleibt unveraendert", async () => {
  const { win, calls } = load();
  await win.fetch("/api/me");
  assert.strictEqual(calls[0].init, undefined);
});

test("fremde Seiten bekommen das Token nie", async () => {
  const { win, calls } = load();
  await win.fetch("https://evil.example/x", { method: "POST" });
  assert.ok(!calls[0].init.headers || !new Headers(calls[0].init.headers).has("X-RA-CSRF"));
});

test("vorhandener Header wird nicht ueberschrieben", async () => {
  const { win, calls } = load();
  await win.fetch("/api/me/prefs", { method: "PUT", headers: { "X-RA-CSRF": "eigen" } });
  assert.strictEqual(new Headers(calls[0].init.headers).get("X-RA-CSRF"), "eigen");
});
