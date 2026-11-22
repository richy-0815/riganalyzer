import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
# Manche Module merken sich den Datenpfad beim ersten Import – nie das echte /data verwenden
import tempfile
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ra-tests-"))
os.environ.setdefault("REPORT", "0")   # Tagesbericht-Thread: in Tests nie echte Mails
os.environ.setdefault("LEZ_REFRESH", "0")
os.environ.setdefault("NEWS_DIGEST", "0")   # Sammelmail-Thread: in Tests nie   # Umweltzonen-Thread: in Tests nie echtes Netz
os.environ.setdefault("FRCAMPS_REFRESH", "0")   # DATAtourisme-Thread: in Tests nie echtes Netz
os.environ.setdefault("PLATZLAENGE_WORKER", "0")   # Stellplatz-Laenge: in Tests kein Hintergrund-Thread


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def enable_admin_2fa(client, origin):
    """Fuer Admin-Tests: 2FA einrichten (setzt das Admin-Sitzungs-Cookie im Client)."""
    import re as _re
    import pyotp
    page = client.get("/2fa-einrichten", headers=origin)
    secret = _re.search(r'data-secret="([A-Z2-7]+)"', page.text).group(1)
    client.post("/2fa-einrichten", data={"code": pyotp.TOTP(secret).now()}, headers=origin)
    return secret
