"""Diesel-Landesschnitt DK/SE/NO (0.32.0): Bulletin-XLSX ohne Zusatzpaket, SSB, EZB, Zuordnung je Land."""
import io
import zipfile

import pytest

import fuelprice_nordic as fn


def _xlsx(rows):
    """Mini-XLSX wie das EU-Bulletin: Strings ueber sharedStrings, Zahlen direkt."""
    strings, idx = [], {}

    def sref(t):
        if t not in idx:
            idx[t] = len(strings)
            strings.append(t)
        return idx[t]
    xml_rows = []
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, v in enumerate(row):
            ref = f"{chr(65 + ci)}{ri}"
            if v is None:
                continue
            if isinstance(v, str):
                cells.append(f'<c r="{ref}" t="s"><v>{sref(v)}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
        xml_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sheet = f'<worksheet {ns}><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    sst = f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/sharedStrings.xml", sst)
    return buf.getvalue()


BULLETIN = _xlsx([
    ["Prices in force on 26/10/2026"],
    [None, "Euro-super 95 (I)", "Automotive gas oil Gasoil automobile (I)", "Heating gas oil"],
    ["Belgium", 1789.0, 1801.5, 950.0],
    ["Denmark", 2001.0, 2502.0, 1500.0],
    ["Sweden", 1650.0, 1712.4, 1400.0],
])


def test_parse_bulletin():
    assert fn.parse_bulletin(BULLETIN) == {"DK": 2.502, "SE": 1.712}


def test_parse_ecb_and_ssb():
    xml = "<Cube currency='USD' rate='1.08'/><Cube currency='NOK' rate='11.50'/><Cube currency='SEK' rate='11.2'/>"
    assert fn.parse_ecb(xml)["NOK"] == 11.5
    meta = {"variables": [{"code": "PetroleumProd", "values": ["031", "035"], "valueTexts": ["Bensin 95", "Autodiesel"]},
                          {"code": "Tid", "values": ["2026M09"], "valueTexts": ["2026M09"]}]}
    assert fn.parse_ssb_meta(meta) == ("PetroleumProd", "035")
    assert fn.parse_ssb_value({"value": [21.85]}) == 21.85
    with pytest.raises(ValueError):
        fn.parse_ssb_value({"value": [None]})


def test_lookup_by_country(monkeypatch, tmp_path):
    import regions
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(fn, "_state", {"t": 0.0, "avg": {}, "loading": False})
    monkeypatch.setattr(fn, "BACKGROUND", False)
    monkeypatch.setattr(fn, "_fetch", lambda: {"DK": 2.502, "SE": 1.712, "NO": 1.9})
    monkeypatch.setattr(regions, "country_at", lambda la, lo: "norway" if la > 59.5 and lo < 11.5 else
                        "sweden" if lo > 12.8 else "denmark")
    st = [("a", 55.4, 11.3), ("b", 59.9, 10.7), ("c", 59.3, 18.1), ("d", 48.1, 11.6)]   # Seeland, Oslo, Stockholm, Muenchen
    out, pending = fn.lookup(st)
    assert pending == set()
    assert {k: (v["cc"], v["diesel"], v["avg"]) for k, v in out.items()} == {
        "a": ("DK", 2.502, True), "b": ("NO", 1.9, True), "c": ("SE", 1.712, True)}
    assert (tmp_path / "fuel-nordic.json").exists()            # Merker fuer den naechsten Start


def test_stale_cache_gives_nothing(monkeypatch):
    monkeypatch.setattr(fn, "_state", {"t": 1.0, "avg": {"DK": 2.5}, "loading": True})
    assert fn.averages() == {}


def test_pending_while_loading(monkeypatch):
    import regions
    monkeypatch.setattr(fn, "_state", {"t": 0.0, "avg": {}, "loading": True})   # laedt gerade (z. B. nach Neustart)
    monkeypatch.setattr(regions, "country_at", lambda la, lo: "norway")
    out, pending = fn.lookup([("b", 59.9, 10.7)])
    assert out == {} and pending == {"NO"}

