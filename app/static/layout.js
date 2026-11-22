/* Layout-Hilfen (Ride + Rig): Ebenen-Knopf in der Karte, gemerkte Ebenen, Profil-Standard, Naechte.
   Reine Funktionen auch in Node (Tests); im Browser window.LayoutUI. Speicher ist Komfort – ohne ihn gelten die
   Standards von heute. */
(function (root) {
  function read(storage, key) {
    try { return storage ? storage.getItem(key) : null; } catch { return null; }
  }
  function write(storage, key, value) {
    try { if (storage) storage.setItem(key, value); } catch { /* privat/gesperrt */ }
  }

  function loadLayers(storage, key, defaults) {
    const raw = read(storage, key);
    if (raw == null) return defaults.slice();
    try {
      const v = JSON.parse(raw);
      return Array.isArray(v) ? v.filter((x) => typeof x === "string") : defaults.slice();
    } catch { return defaults.slice(); }
  }

  function saveLayers(storage, key, layers) { write(storage, key, JSON.stringify(layers)); }

  /** Profil offen? Gemerkte Wahl zuerst, sonst offen ab 850 px Fensterhoehe – und immer am Handy (≤ 900 px breit,
   *  dort hat die Karte eine feste Hoehe, Zuklappen braechte nichts). */
  function profileOpen(storage, key, innerHeight, innerWidth) {
    const v = read(storage, key);
    if (v === "1" || v === "0") return v === "1";
    return (innerWidth !== undefined && innerWidth <= 900) || innerHeight >= 850;
  }

  function nightsStep(value, delta) {
    const n = parseInt(value, 10);
    return Math.max(0, Math.min(30, (Number.isFinite(n) ? n : 0) + delta));
  }

  /** Haekchenreihe in ein Leaflet-Feld „🗂 Ebenen“ oben rechts verschieben; Auswahl merken. */
  function mountLayerButton(map, togglesEl, key, onChange) {
    const L = root.L;
    let store = null;
    try { store = root.localStorage; } catch { store = null; }
    const boxes = Array.from(togglesEl.querySelectorAll("input[type=checkbox]"));
    const name = (cb) => cb.dataset.m || (cb.id === "cov-toggle" ? "cov" : cb.id);
    const want = new Set(loadLayers(store, key, boxes.filter((cb) => cb.checked).map(name)));
    boxes.forEach((cb) => {
      const on = want.has(name(cb));
      if (cb.checked !== on) { cb.checked = on; cb.dispatchEvent(new Event("change", { bubbles: true })); }
    });
    togglesEl.addEventListener("change", () => saveLayers(store, key, boxes.filter((cb) => cb.checked).map(name)));

    const Ctl = L.Control.extend({
      options: { position: "topright" },
      onAdd() {
        const box = L.DomUtil.create("div", "layerctl");
        const btn = L.DomUtil.create("button", "layerbtn", box);
        btn.type = "button";
        btn.textContent = "🗂 Ebenen";
        btn.setAttribute("aria-expanded", "false");
        btn.setAttribute("aria-controls", togglesEl.id);
        const panel = L.DomUtil.create("div", "layerpanel", box);
        panel.hidden = true;
        panel.append(togglesEl);
        L.DomEvent.disableClickPropagation(box);
        L.DomEvent.disableScrollPropagation(box);
        const set = (open) => { panel.hidden = !open; btn.setAttribute("aria-expanded", String(open)); };
        btn.addEventListener("click", () => set(panel.hidden));
        document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) { set(false); btn.focus(); } });
        document.addEventListener("click", (e) => { if (!box.contains(e.target)) set(false); });
        return box;
      },
    });
    new Ctl().addTo(map);
    if (onChange) onChange();
  }

  /** Route ganz zeigen: zwei Frames warten, bis Profil/Ergebniskarten die Karte umgeformt haben,
      dann Groesse neu lesen und mit festem Rand einpassen – sonst schneidet die geschrumpfte Karte oben/unten ab. */
  function fitWhenSettled(map, bounds) {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      map.invalidateSize();
      map.fitBounds(bounds, { padding: [24, 24], animate: false });
    }));
  }

  const api = { loadLayers, saveLayers, profileOpen, nightsStep, mountLayerButton, fitWhenSettled };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.LayoutUI = api;
})(typeof window !== "undefined" ? window : globalThis);
