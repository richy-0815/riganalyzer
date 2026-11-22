"""Hilfen fuer Tests: Valhalla-Polyline (1e6) kodieren."""


def enc(pts, f=1e6):
    out, pla, plo = [], 0, 0
    for la, lo in pts:
        for v, prev in ((round(la * f), pla), (round(lo * f), plo)):
            d = v - prev
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1f)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        pla, plo = round(la * f), round(lo * f)
    return "".join(out)
