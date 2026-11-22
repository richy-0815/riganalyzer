/* Wartetext der Analysen: Der Server schickt bei wartenden Jobs „Platz N in der Warteschlange · noch ca. M Min.“;
   ältere Server (nur „wartet“) bekommen den bisherigen Text. Reine Funktion, auch für node --test; im Browser window.QueueText. */
(function (root) {
  const OLD = "wartet auf einen freien Platz …";
  function label(j) {
    if (j.state === "queued") return typeof j.msg === "string" && j.msg.startsWith("Platz ") ? j.msg : OLD;
    return j.msg;
  }
  const api = { label };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.QueueText = api;
})(typeof window !== "undefined" ? window : globalThis);
