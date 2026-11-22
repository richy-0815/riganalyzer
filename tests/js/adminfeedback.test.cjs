// node --test tests/js/  –  Admin „Rückmeldungen“: Ticket-Status
const test = require("node:test");
const assert = require("node:assert");
const F = require("../../app/static/admin-feedback.js");

test("ticketLabel: ohne, vorgemerkt, angelegt", () => {
  assert.equal(F.ticketLabel(undefined), "🎫 Als Ticket vormerken");
  assert.equal(F.ticketLabel("vorgemerkt"), "🎫 vorgemerkt – zurücknehmen");
  assert.equal(F.ticketLabel({ nr: 12, status: "offen" }), "🎫 Ticket #12 · offen");
  assert.equal(F.ticketLabel({ nr: 12, status: "erledigt" }), "🎫 Ticket #12 · erledigt");
});

// erledigte löschen
test("bulkLabel: Anzahl im Sammelknopf", () => {
  assert.equal(F.bulkLabel(3), "🗑 Erledigte löschen (3)");
});

test("confirmOne: Login und Datum, Hinweis bei Vormerkung", () => {
  const x = { user: "olaf", t: "2026-11-10T09:00:00" };
  assert.equal(F.confirmOne(x), "Rückmeldung von olaf vom 10.11.2026 endgültig löschen?");
  assert.match(F.confirmOne({ ...x, ticket: "vorgemerkt" }), /vorgemerkt – es wird dann keins mehr angelegt/);
  assert.doesNotMatch(F.confirmOne({ ...x, ticket: { nr: 3, status: "offen" } }), /vorgemerkt/);
});

test("confirmBulk: zählt nur erledigte, nennt Vormerkungen", () => {
  const items = [{ done: true }, { done: true, ticket: "vorgemerkt" }, { done: false, ticket: "vorgemerkt" }];
  assert.equal(F.confirmBulk(items).split("\n")[0], "2 erledigte Rückmeldungen endgültig löschen?");
  assert.match(F.confirmBulk(items), /1 davon als Ticket vorgemerkt/);
  assert.equal(F.confirmBulk([{ done: true }]), "Eine erledigte Rückmeldung endgültig löschen?");
});
