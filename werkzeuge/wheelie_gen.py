"""Erzeugt das Wheelie-SVG (klassisches Naked Bike, Retro-Roadster) fuer den Ride-Fortschrittsbalken.
Bike in Waagerecht-Koordinaten gezeichnet (Boden y=50), dann um den Hinterrad-Aufstandspunkt hochgekippt."""
import base64
import math
import sys

R = 10.0                 # Radradius
RX, FX, AY = 16.0, 64.0, 40.0   # Hinterachse, Vorderachse, Achshoehe
PITCH = 27               # Wheelie-Winkel
OUT = "#1b1d20"          # Kontur


def wheel(cx, cy, dur, cast=False):
    spokes = "".join(
        f'<path d="M{cx} {cy} L{cx + 7.4 * math.cos(math.radians(a)):.2f} {cy + 7.4 * math.sin(math.radians(a)):.2f}"/>'
        for a in range(0, 360, 72 if cast else 30))
    sw = "1.5" if cast else ".45"
    return (f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="#16181a"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{R - 0.6}" fill="none" stroke="#3a3f44" stroke-width=".5"/>'
            f'<circle cx="{cx}" cy="{cy}" r="7.6" fill="#2a2e32" stroke="#c9d0d4" stroke-width=".9"/>'
            f'<g stroke="#c9d0d4" stroke-width="{sw}" stroke-linecap="round">{spokes}'
            f'<animateTransform attributeName="transform" type="rotate" from="0 {cx} {cy}" to="360 {cx} {cy}" '
            f'dur="{dur}" repeatCount="indefinite"/></g>'
            f'<circle cx="{cx}" cy="{cy}" r="1.8" fill="#8c959b" stroke="#e5e9eb" stroke-width=".4"/>')


def bike():
    p = []
    # Schutzblech hinten + Heck
    p.append(f'<path d="M{RX - 9} {AY - 7} Q{RX - 2} {AY - 14} {RX + 8} {AY - 13}" fill="none" stroke="#7a1418" stroke-width="2" stroke-linecap="round"/>')
    # Schwinge
    p.append(f'<path d="M{RX} {AY} L37 36.5" stroke="#2b2f33" stroke-width="2.6" stroke-linecap="round"/>')
    p.append(f'<path d="M{RX} {AY - .6} L37 35.9" stroke="#9aa3a8" stroke-width=".6"/>')
    # Rahmen (Doppelschleife, schwarz)
    p.append('<path d="M37 36.5 L35 24 L54 17 L57 15 M54 17 L52 30 L55 39 L40 40.5 Z" fill="none" stroke="#25282b" '
             'stroke-width="1.6" stroke-linejoin="round"/>')
    # Federbein: gelbe Feder (Oehlins-Stil)
    sx0, sy0, sx1, sy1 = RX + 3, AY - 2.5, 27.5, 22.5
    p.append(f'<path d="M{sx0} {sy0} L{sx1} {sy1}" stroke="#bfc6ca" stroke-width="1.4" stroke-linecap="round"/>')
    coils = []
    for i in range(7):
        t = 0.22 + i * 0.085
        x, y = sx0 + (sx1 - sx0) * t, sy0 + (sy1 - sy0) * t
        coils.append(f'M{x - 1.6:.2f} {y - 0.4:.2f} L{x + 1.6:.2f} {y + 0.9:.2f}')
    p.append(f'<path d="{" ".join(coils)}" stroke="#f2c500" stroke-width="1.1" stroke-linecap="round"/>')
    # Motor: Kurbelgehaeuse + luftgekuehlter Reihenvierer mit Kuehlrippen
    p.append('<path d="M36 33 Q36 41 42 41.5 L54 41.5 Q57 41 57 37 L56 33 Z" fill="#3a3f44" stroke="#0e0f10" stroke-width=".6"/>')
    p.append('<circle cx="41" cy="36.8" r="2.6" fill="#aab3b8" stroke="#555d62" stroke-width=".5"/>')
    p.append('<path d="M38.5 23.5 L55.5 22 L56.5 33.5 L37.5 33.5 Z" fill="#b8c0c5" stroke="#4a5257" stroke-width=".6"/>')
    fins = " ".join(f"M{37.8 + i * .07:.2f} {24.7 + i * 1.75:.2f} H{56.0 + i * .06:.2f}" for i in range(6))
    p.append(f'<path d="{fins}" stroke="#5f686d" stroke-width=".55"/>')
    p.append('<path d="M38 23.4 L56 21.8 L56.2 23 L38.1 24.6 Z" fill="#d7dde0"/>')
    # Vergaser/Luftfilter-Schatten hinter dem Motor
    p.append('<rect x="33.5" y="25" width="4.5" height="5" rx="1" fill="#2b2f33"/>')
    # 4-in-1-Auspuff: Kruemmer vorn runter, unter dem Motor durch, Topf schraeg nach hinten oben
    for i, dx in enumerate((0, 1.1, 2.2)):
        p.append(f'<path d="M{54.5 - dx} 31 Q{58.5 - dx} {37 + i * .3} {52 - dx} {42.6 + i * .25} L41 {43.3 + i * .2}" '
                 f'fill="none" stroke="#dfe5e8" stroke-width="1" stroke-linecap="round"/>')
        p.append(f'<path d="M{54.5 - dx} 31 Q{58.5 - dx} {37 + i * .3} {52 - dx} {42.6 + i * .25} L41 {43.3 + i * .2}" '
                 f'fill="none" stroke="#6c757a" stroke-width=".25"/>')
    p.append('<path d="M42 42.2 L22 37.2 L20.6 39.6 Q20 41.6 21.8 42 L41 45.2 Z" fill="#e3e8ea" stroke="#5f686d" stroke-width=".6"/>')
    p.append('<path d="M40 43.3 L22 39.1" stroke="#ffffff" stroke-width=".7" opacity=".8"/>')
    p.append('<ellipse cx="21.3" cy="39.6" rx="1" ry="2.3" transform="rotate(14 21.3 39.6)" fill="#3a3f44"/>')
    # Seitendeckel
    p.append('<path d="M27 23.5 L36 23.5 L35 30 Q30 31 27.5 28 Z" fill="#8e1a1f" stroke="#0e0f10" stroke-width=".5"/>')
    # Tank: Tropfenform, tiefrot mit Goldlinie
    p.append('<path d="M35 22 Q35.5 14.5 44 13.2 Q52 12.4 56 16.4 Q57 18.6 54.8 20.4 L38 23.8 Q35 23.6 35 22 Z" '
             'fill="#b3141c" stroke="#0e0f10" stroke-width=".7"/>')
    p.append('<path d="M37.5 19.8 Q43 16.2 54.6 17.6" fill="none" stroke="#e8c15a" stroke-width=".8"/>')
    p.append('<path d="M39 16.6 Q45 14 51 14.3" fill="none" stroke="#ffffff" stroke-width="1" opacity=".55" stroke-linecap="round"/>')
    # Sitzbank flach + Heckbuerzel mit Ruecklicht
    p.append('<path d="M14 20.6 Q14 18.4 17 18.4 L36.5 18.6 Q38 19 37.5 21.4 L36 23.5 L16 23 Q14 22.8 14 20.6 Z" '
             'fill="#1a1a1c" stroke="#0e0f10" stroke-width=".5"/>')
    p.append('<path d="M16 18.7 L36 18.9" stroke="#55595e" stroke-width=".5"/>')
    p.append('<path d="M10.5 22.2 Q11 20.6 14.5 20.8 L26 23.4 L26 25.6 L12 25 Q10.3 24.4 10.5 22.2 Z" fill="#b3141c" stroke="#0e0f10" stroke-width=".5"/>')
    p.append('<rect x="9.3" y="22.2" width="1.8" height="2.4" rx=".5" fill="#ff3b3b"/>')
    # Telegabel (Gabelfuss silber, Standrohr chrom) + Gabelbruecke
    fk0, fk1 = (57, 13.5), (FX, AY)
    p.append(f'<path d="M{fk0[0]} {fk0[1]} L{fk1[0]} {fk1[1]}" stroke="#2b2f33" stroke-width="3.2" stroke-linecap="round"/>')
    p.append(f'<path d="M{fk0[0] + .3} {fk0[1] + 1} L{fk0[0] + 4.3} {fk0[1] + 16}" stroke="#e8edef" stroke-width="1.7" stroke-linecap="round"/>')
    # Schutzblech vorn
    p.append(f'<path d="M{FX - 8} {AY - 7.5} Q{FX + 1} {AY - 14.5} {FX + 9.5} {AY - 6}" fill="none" stroke="#b3141c" '
             'stroke-width="1.9" stroke-linecap="round"/>')
    # Lenker + Spiegel
    p.append('<path d="M56 12.8 L54 9.6 L49.8 9" fill="none" stroke="#c9d0d4" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>')
    p.append('<path d="M55 10.2 L55.8 5.8" stroke="#c9d0d4" stroke-width=".6"/>')
    p.append('<circle cx="56" cy="4.6" r="1.5" fill="#2b2f33" stroke="#e8edef" stroke-width=".6"/>')
    # Rundscheinwerfer mit Chromring + Rundinstrumente
    p.append('<circle cx="58.4" cy="11.6" r="1.6" fill="#2b2f33" stroke="#c9d0d4" stroke-width=".4"/>')
    p.append('<circle cx="61.4" cy="16.4" r="4.4" fill="#d9dfe2" stroke="#0e0f10" stroke-width=".6"/>')
    p.append('<circle cx="61.9" cy="16.4" r="3.2" fill="#fff6c8" stroke="#9aa3a8" stroke-width=".5"/>')
    p.append('<circle cx="62.6" cy="15.6" r="1" fill="#ffffff"/>')
    return "".join(p)


def rider():
    p = []
    # Bein: Oberschenkel am Tank, Unterschenkel zur Fussraste, Stiefel
    p.append('<path d="M30 18 L43 20.6 L37.5 32" fill="none" stroke="#24324a" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')
    p.append('<path d="M35.6 31.2 L40.6 32.6 L40.4 34.4 L35 34.2 Z" fill="#111" />')
    p.append('<rect x="36" y="34" width="5" height=".9" fill="#9aa3a8"/>')
    # Oberkoerper Lederjacke, leicht vorgebeugt (im Wheelie aufrecht)
    p.append('<path d="M26.5 18.5 L28.8 4.2 Q33 1.6 37.4 3.6 L35.6 19 Q31 20.6 26.5 18.5 Z" fill="#26282b" stroke="#0e0f10" stroke-width=".5"/>')
    p.append('<path d="M29 9 L36.6 9.6" stroke="#b3141c" stroke-width="1.1"/>')
    # Arme zum Lenker
    p.append('<path d="M34.8 5.4 L43.5 9.2 L49.8 9.4" fill="none" stroke="#26282b" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>')
    p.append('<circle cx="50" cy="9.3" r="1.2" fill="#111"/>')
    # Helm (Jethelm-Retro, weiss mit rotem Streifen) + Visier
    p.append('<circle cx="33.6" cy="-1.4" r="4.4" fill="#f1f1ee" stroke="#0e0f10" stroke-width=".6"/>')
    p.append('<path d="M30.2 -4 Q33.6 -6.6 37.4 -3.6" fill="none" stroke="#b3141c" stroke-width="1.1"/>')
    p.append('<path d="M34.6 -2.2 Q37.6 -2.8 38 -0.4 L38 0.8 Q36 1.4 34.4 0.6 Z" fill="#20262c" stroke="#0e0f10" stroke-width=".3"/>')
    return "".join(p)


def svg():
    cx, cy = RX, AY + R   # Aufstandspunkt hinten
    rot = (f'<animateTransform attributeName="transform" type="rotate" values="-{PITCH} {cx} {cy};-{PITCH - 3} {cx} {cy};-{PITCH} {cx} {cy}" '
           f'keyTimes="0;.5;1" dur="1.6s" calcMode="spline" keySplines=".45 0 .55 1;.45 0 .55 1" repeatCount="indefinite"/>')
    dust = "".join(
        f'<circle cx="{cx - 2}" cy="{cy - 2}" r="2.2" fill="#a59c8e" opacity="0">'
        f'<animate attributeName="opacity" values=".55;0" dur=".9s" begin="{d}s" repeatCount="indefinite"/>'
        f'<animate attributeName="r" values="1.6;4.2" dur=".9s" begin="{d}s" repeatCount="indefinite"/>'
        f'<animateTransform attributeName="transform" type="translate" values="0 0;-11 -3" dur=".9s" begin="{d}s" repeatCount="indefinite"/></circle>'
        for d in (0, .3, .6))
    body = (f'<g transform="rotate(-{PITCH} {cx} {cy})">{rot}'
            f'{wheel(RX, AY, "0.3s")}{wheel(FX, AY, "1.6s")}{bike()}{rider()}</g>')
    vb = sys.argv[1] if len(sys.argv) > 1 else "-4 -22 100 74"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}">'
            f'<ellipse cx="{cx + 4}" cy="{cy + .6}" rx="13" ry="1.6" fill="#000" opacity=".28"/>{dust}{body}</svg>')


if __name__ == "__main__":
    s = svg()
    open(__file__.replace("wheelie_gen.py", "wheelie.svg"), "w").write(s)
    print(base64.b64encode(s.encode()).decode())
