"""Technik-Seite verraet keine Angriffsflaeche: keine Geraete-/System-/Versionsangaben, keine Hostnamen."""
import re

import main

LEAKS = ["FRITZ", "Raspberry", "Kernel", "Docker", "Traefik", "fail2ban", "MacBook", "macOS",
         "python:3", "Speicherlimit", "Mbit", "nur DE", "nur aus Deutschland", "Ansible", "uvicorn"]


def test_tech_page_has_no_infrastructure_details(monkeypatch):
    monkeypatch.setattr(main, "_live_values", lambda: {"app": "9.9.9", "fastapi": "0.1.2", "python": "3.14.1",
                                                      "lisa_kernel": "6.12.0", "valhalla": "3.9.1", "regions": "159"})
    html = main._tech_html(True)
    body = html[html.index('<div class="tech">'):]
    for word in LEAKS:
        assert word.lower() not in body.lower(), word
    # nur die eigene App-Version, keine Bibliotheks-/Systemversionen
    assert not re.search(r"\b(0\.1\.2|3\.14\.1|6\.12\.0|3\.9\.1)\b", body)
