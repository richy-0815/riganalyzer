"""Schluessel aus Log-Zeilen tilgen (seit 0.47.1).

httpx loggt jede Anfrage mit voller URL – Tankerkoenig (apikey=…) und Mapillary (access_token=…) tragen ihren
Schluessel aber als Query-Parameter. Auch Fehlermeldungen von httpx (HTTPStatusError, Tracebacks) nennen die URL.
Deshalb setzt install() eine Record-Factory: jede Log-Zeile wird beim Entstehen geschwaerzt – unabhaengig davon,
welcher Logger oder Handler (Konsole, Tagesbericht, uvicorn) sie spaeter ausgibt.
"""
import logging
import re

# Query-/Formular-Parameter, deren Wert nie ins Log darf (Gross/Klein egal).
SECRET_PARAMS = ("apikey", "api_key", "access_token", "token", "key", "client_secret", "password")
_RE = re.compile(r"(?i)([?&;](?:%s)=)[^&#\s\"'<>]+" % "|".join(SECRET_PARAMS))
MASK = "***"


def redact(text):
    """Werte geheimer Query-Parameter durch *** ersetzen."""
    return _RE.sub(r"\g<1>" + MASK, text) if text else text


_installed = False


def install():
    """Record-Factory einhaengen (idempotent)."""
    global _installed
    if _installed:
        return
    base = logging.getLogRecordFactory()
    fmt = logging.Formatter()

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        try:
            msg = record.getMessage()
            clean = redact(msg)
            if clean != msg:
                record.msg, record.args = clean, None
            if record.exc_info and not record.exc_text:
                record.exc_text = redact(fmt.formatException(record.exc_info))
        except Exception:   # noqa: BLE001 – Logging darf nie selbst scheitern
            pass
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
