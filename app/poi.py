"""POI-Einstufung aus OSM-Tags (Kern, seit 0.46.4 aus rig.py herausgeloest).

Tankstellen (Diesel, AdBlue, Preisklasse), Plaetze (Ver-/Entsorgung, Masse, Ausstattung), Wasserzugang und
Masseinheiten. Wird von analyze, nights und den Rig-Modulen benutzt – deshalb im Kern, nicht in rig.py.
"""

import re

def measure(v):
    """'3.5', '3,5 m', '11'6\"', '7.5 t', '3500 kg' -> Meter bzw. Tonnen (float) oder None."""
    if v is None:
        return None
    s = str(v).strip().lower().replace(",", ".")
    if s in ("", "none", "no", "default", "unsigned", "below_default", "unknown"):
        return None
    m = re.match(r"^(\d+)\s*'\s*(?:(\d+(?:\.\d+)?)\s*\"?)?$", s)          # Fuss/Zoll
    if m:
        return round(int(m.group(1)) * 0.3048 + float(m.group(2) or 0) * 0.0254, 2)
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(m|t|kg|st|lbs|ft)?\b", s)
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2)
    if unit == "kg":
        val /= 1000
    elif unit == "st":
        val *= 0.907
    elif unit == "lbs":
        val *= 0.000453592
    elif unit == "ft":
        val *= 0.3048
    return round(val, 2)


# Preisklassen fuer den Tankplan (ohne Live-Preise): Raststaette teuer, Autohof/Marke etwas ueber,
# freie Tankstellen und Supermaerkte darunter. Abschlaege je Klasse stehen in static-rig/fuelplan.js.
DISCOUNT_BRANDS = ("jet", "bft", "raiffeisen", "globus", "kaufland", "hem", "q1", "score", "classic", "orlen",
                   "e.leclerc", "leclerc", "intermarché", "intermarche", "carrefour", "auchan", "super u", "système u",
                   "systeme u", "hyper u", "casino", "lidl", "aldi", "netto", "migrol", "coop", "landi", "turmöl", "diskont")
MAJOR_BRANDS = ("aral", "shell", "esso", "totalenergies", "total", "bp", "agip", "eni", "omv", "avia", "q8",
                "westfalen", "star", "tamoil", "ip", "repsol", "cepsa", "galp", "mol", "petrom", "lukoil", "texaco")


def fuel_class(tg, motorway):
    """'rast' (Autobahn-Raststaette) | 'autohof' | 'discount' | 'marke' | 'frei'."""
    if motorway:
        return "rast"
    txt = " ".join(str(tg.get(k, "")) for k in ("name", "operator", "brand")).lower()
    if "autohof" in txt or "truckstop" in txt or "truck stop" in txt:
        return "autohof"
    brand = (tg.get("brand") or tg.get("operator") or tg.get("name") or "").strip().lower()
    if any(brand == b or brand.startswith(b + " ") for b in DISCOUNT_BRANDS):
        return "discount"
    if any(brand == b or brand.startswith(b + " ") for b in MAJOR_BRANDS):
        return "marke"
    return "frei"


def poi_flags(tg, group):
    """Zusatzinfos fuer Tankstellen/Stellplaetze (seit 0.47.0 auch Essen: Zeiten fuer „jetzt geöffnet“, Ticket #4)."""
    if group == "food":
        return {"hours": tg.get("opening_hours", ""), "website": tg.get("website") or tg.get("contact:website") or "", **_contact(tg)}
    if group == "sight":
        return {k: v for k, v in {"hours": tg.get("opening_hours", ""), "website": tg.get("website") or tg.get("contact:website") or "",
                                  "wikipedia": tg.get("wikipedia", ""), **_contact(tg)}.items() if v} or None
    if group == "fuel":
        # reine Gas-Stationen (Autogas/Fluessiggas) ohne Diesel-Angabe: kein Diesel
        gas_only = "fuel:diesel" not in tg and bool(re.search(r"fl(ü|ue)ssiggas|autogas|\blpg\b|\bgpl\b|\bcng\b|erdgas",
                                                                (tg.get("name", "") + " " + tg.get("operator", "")).lower()))
        return {"diesel": tg.get("fuel:diesel") != "no" and not gas_only,
                "diesel_known": "fuel:diesel" in tg,
                "adblue": tg.get("fuel:adblue") == "yes",
                "adblue_known": "fuel:adblue" in tg,
                "truck": tg.get("hgv") in ("yes", "designated") or tg.get("fuel:HGV_diesel") == "yes",
                "lpg": tg.get("fuel:lpg") == "yes",
                "hours": tg.get("opening_hours", ""), "brand": tg.get("brand", ""),
                # nur mit Lkw-Tankkarte (AS 24): fuer Wohnmobile nutzlos
                "card_only": bool(re.match(r"^as ?24\b", (tg.get("brand") or tg.get("name") or "").lower()))}
    if group == "camp":
        return {"dump": tg.get("amenity") == "sanitary_dump_station" or tg.get("sanitary_dump_station") in ("yes", "customers"),
                "water": tg.get("amenity") in ("water_point", "drinking_water") or tg.get("water_point") == "yes",
                "power": tg.get("power_supply") in ("yes",) or bool(re.match(r"^\d", str(tg.get("power_supply", "")))),
                "fee": _yesno(tg.get("fee")), "charge": tg.get("charge", ""), "capacity": tg.get("capacity", ""),
                "maxlength": measure(tg.get("maxlength")), "maxweight": _tonnes(tg.get("maxweight")),
                "hours": tg.get("opening_hours", ""), "seasonal": tg.get("seasonal") or None,
                "shower": _yesno(tg.get("shower")), "toilets": _yesno(tg.get("toilets")),
                "wifi": None if not tg.get("internet_access") else tg["internet_access"] not in ("no",),
                "laundry": _yesno(tg.get("laundry")),
                "dog": tg.get("dog") if tg.get("dog") in ("yes", "no", "leashed") else None,
                "website": tg.get("website") or tg.get("contact:website") or "",
                "phone": tg.get("phone") or tg.get("contact:phone") or "",
                "kind": tg.get("tourism") if tg.get("tourism") in ("caravan_site", "camp_site") else None,
                # was OSM sonst noch weiss (Anzeige im Platz-Popup, Hoehe fuer „passt“)
                "maxheight": measure(tg.get("maxheight")),
                "motorhome": _yesno(tg.get("motorhome")), "caravans": _yesno(tg.get("caravans")),
                "surface": tg.get("surface", ""), "stars": tg.get("stars", ""),
                "operator": tg.get("operator", ""), "reservation": tg.get("reservation", ""),
                "email": tg.get("email") or tg.get("contact:email") or "",
                "drinking_water": _yesno(tg.get("drinking_water")),
                "check_date": (tg.get("check_date") or tg.get("survey:date") or "")[:10],
                "description": (tg.get("description:de") or tg.get("description") or "")[:500],
                "image": tg.get("image", "") if str(tg.get("image", "")).startswith("https://") else "",
                "commons": tg.get("wikimedia_commons", ""),
                "city": tg.get("addr:city", "")}
    return None


def _contact(tg):
    """Telefon, Beschreibung und Foto fuer die Detail-Karte (0.54.0, #31) – nur, was OSM wirklich hat."""
    return {k: v for k, v in {"phone": tg.get("phone") or tg.get("contact:phone") or "",
                              "description": (tg.get("description:de") or tg.get("description") or "")[:500],
                              "commons": tg.get("wikimedia_commons", ""),
                              "image": tg.get("image", "") if str(tg.get("image", "")).startswith("https://") else ""}.items() if v}


def _yesno(v):
    """OSM yes/no -> True/False, fehlend oder unklar -> None (Anzeige „?“)."""
    if v in ("yes", "customers", "designated", "free", "hot"):
        return True
    if v == "no":
        return False
    return None


def _tonnes(v):
    """'7.5', '7,5 t', '3500 kg' -> Tonnen (measure rechnet kg/lbs schon um)."""
    return measure(v)


# Wasser darf nur als „oeffentlich“ gelten, wenn man ohne Uebernachtung hinkommt
CLOSED_ACCESS = {"private", "customers", "no", "permit"}


def water_kind(tg, in_camp):
    """public: freie Zapfstelle ausserhalb eines Platzes · guests: auf dem Platz bzw. nur fuer Kunden ·
    tap: oeffentlicher Trinkwasserhahn (eher fuer Kanister) · None: kein Wasser bzw. nicht zugaenglich."""
    amen = tg.get("amenity")
    if amen in ("water_point", "drinking_water"):
        acc = tg.get("access")
        if acc in ("private", "no", "permit"):
            return None
        if in_camp or acc == "customers":
            return "guests"
        return "public" if amen == "water_point" or tg.get("water_point") == "yes" else "tap"
    if tg.get("tourism") in ("caravan_site", "camp_site") and tg.get("water_point") == "yes":
        return "guests"
    return None


_OSM_TYPES = {"n": "node", "w": "way", "r": "relation"}


def osm_ref(osm):
    """Datensatz-Id n123/w123/r123 -> node/123, way/123, relation/123 (Link im Popup)."""
    s = str(osm or "")
    return f"{_OSM_TYPES[s[0]]}/{s[1:]}" if s[:1] in _OSM_TYPES and s[1:].isdigit() else s
