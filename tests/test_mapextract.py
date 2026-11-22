import json
import os

import mapextract


class FakeResp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def test_latest_build_takes_last_key():
    def fetch(url, **kw):
        return FakeResp([{"key": "20261024.pmtiles"}, {"key": "20261025.pmtiles"}])

    assert mapextract.latest_build(fetch) == "https://build.protomaps.com/20261025.pmtiles"


def test_extract_calls_pmtiles(tmp_path):
    calls = []
    mapextract.extract("/x/region.geojson", str(tmp_path / "karte.pmtiles"), "https://b/1.pmtiles",
                       run=lambda cmd, **kw: calls.append((cmd, kw)))
    cmd, kw = calls[0]
    assert cmd == ["pmtiles", "extract", "https://b/1.pmtiles", str(tmp_path / "karte.pmtiles"),
                   "--region=/x/region.geojson", "--maxzoom=14", "--download-threads=4"]
    assert kw.get("check") is True


def test_copy_style(tmp_path):
    src = tmp_path / "navistyle"
    (src / "fonts" / "Noto Sans Regular").mkdir(parents=True)
    (src / "fonts" / "Noto Sans Regular" / "0-255.pbf").write_bytes(b"x")
    (src / "sprites").mkdir()
    (src / "sprites" / "hell.png").write_bytes(b"p")
    for n in ("hell.json", "dunkel.json"):
        (src / n).write_text(json.dumps({"sources": {"protomaps": {"url": "pmtiles://{KARTE}"}}}))
    dst = tmp_path / "paket"
    mapextract.copy_style(str(dst), src=str(src))
    assert {"hell.json", "dunkel.json", "fonts", "sprites"} <= set(os.listdir(dst / "stil"))
    assert (dst / "stil" / "fonts" / "Noto Sans Regular" / "0-255.pbf").read_bytes() == b"x"
