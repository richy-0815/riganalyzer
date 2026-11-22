"""Erweiterungspunkte des Kerns (seit 0.46.4, Trennung Ride/Rig Phase 1).

Der Kern kennt keine App-Module. Was eine App zusaetzlich braucht (Rig: Fahrzeugpruefung in der Analyse, Laeden an
der Route, Touren/Pakete/Bewertungen beim Loeschen und in „Meine Daten“), meldet sie hier an:

    hooks.add("purge.user", fn)

Die Erweiterungs-Module stehen in RA_EXTENSIONS (Komma-Liste) und werden beim ersten Bedarf geladen – so greift die
Anmeldung auch in Werkzeugen wie `users.py del`, nicht nur im Webserver. Fehlt ein Modul, ist das ein Fehler und
kein stilles Weglassen: beim Loeschen eines Logins duerfen keine Daten liegen bleiben.

Uebergang: Solange ein Container beide Apps bedient, ist "rig_ext" voreingestellt. In Phase 2 setzt jeder Container
seine Liste selbst (Ride: leer, Rig: rig_ext).
"""

import importlib
import os
import threading

_hooks = {}
_loaded = None
_lock = threading.RLock()   # reentrant: ein Erweiterungs-Import darf selbst hooks benutzen
DEFAULT_EXTENSIONS = "rig_ext"


def extensions():
    # Ride-Container (RA_APP=ride) laedt ohne ausdrueckliche Angabe keine Rig-Erweiterung
    default = "" if os.environ.get("RA_APP", "").strip() == "ride" else DEFAULT_EXTENSIONS
    raw = os.environ.get("RA_EXTENSIONS", default)
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def load():
    """Erweiterungen einmalig importieren (die melden sich beim Import per add() an)."""
    global _loaded
    with _lock:
        if _loaded is not None:
            return
        for name in extensions():
            importlib.import_module(name)
        _loaded = extensions()


def add(name, fn):
    lst = _hooks.setdefault(name, [])
    if fn not in lst:
        lst.append(fn)
    return fn


def all_(name):
    load()
    return list(_hooks.get(name, []))


def one(name):
    """Genau eine Anmeldung oder None (z. B. Fahrzeugpruefung: nur eine App liefert sie)."""
    fns = all_(name)
    if len(fns) > 1:
        raise RuntimeError(f"Erweiterungspunkt {name} mehrfach belegt")
    return fns[0] if fns else None
