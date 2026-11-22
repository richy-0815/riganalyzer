"""Schluessel in URLs (Tankerkoenig apikey, Mapillary access_token) landen nie im Log (0.47.1)."""
import logging

import httpx

import logredact

FAKE = "00000000-dead-beef-0000-123456789abc"
TK = ("GET https://creativecommons.tankerkoenig.de/json/list.php?lat=49.98&lng=7.55&rad=5"
      "&sort=dist&type=diesel&apikey=" + FAKE)


def test_redact_query_params():
    out = logredact.redact(TK)
    assert FAKE not in out and "apikey=***" in out and "lat=49.98" in out
    assert logredact.redact("https://graph.mapillary.com/images?access_token=MLY|1|x&limit=50") \
        == "https://graph.mapillary.com/images?access_token=***&limit=50"
    assert logredact.redact("x?APIKEY=geheim") == "x?APIKEY=***"
    # harmlose Parameter bleiben
    assert logredact.redact("GET /route?monkey=1&keyboard=2") == "GET /route?monkey=1&keyboard=2"
    assert logredact.redact("") == "" and logredact.redact(None) is None


def test_log_records_redacted(caplog):
    logredact.install()
    logredact.install()   # idempotent
    with caplog.at_level(logging.INFO):
        # wie httpx: Format-String + Args (die URL steckt in den Args)
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s"', "GET", httpx.URL(TK.split(" ")[1]), "200 OK")
        try:
            raise RuntimeError("Fehler bei " + TK)
        except RuntimeError:
            logging.getLogger("fuelprice").exception("Abruf gescheitert")
    assert FAKE not in caplog.text
    assert caplog.text.count("apikey=***") >= 2


def test_main_installs():
    import main  # noqa: F401
    assert logredact._installed
