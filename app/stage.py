"""QA-Stage (seit v0.21.0): gelbes Band „QA-Testumgebung“ auf jeder HTML-Seite.

ASGI-Middleware, in main.py VOR der GZip-Middleware registriert (= weiter innen), sieht also den unkomprimierten
Text. Nur aktiv mit STAGE=qa."""

import os
import re
from html import escape

# Knopf „✅ OK → nach Prod“: fuehrt zum CI-Ablauf qa-nach-prod.
# Nur ein Link – die QA-App selbst hat keine Rechte auf das Repo; ausgeloest wird in der CI mit der Anmeldung des Admins.
PROD_URL = os.environ.get("QA_PROD_URL", "https://forgejo.example/actions?workflow=qa-nach-prod.yml")


def banner_html(body: bytes, version: str, git_ref: str = "") -> bytes:
    """Band hinter das erste <body…>; ohne <body> unveraendert."""
    m = re.search(rb"<body[^>]*>", body)
    if not m:
        return body
    txt = f"QA-Testumgebung · Version {version}" + (f" · {git_ref}" if git_ref else "") + " · keine echten Daten, keine Mails"
    branch = git_ref.split("@")[0]
    btn = "" if not branch or branch == "master" else (
        f' <a href="{escape(PROD_URL)}" target="_blank" rel="noopener" title="Forgejo: qa-nach-prod mit Branch {escape(branch)} ausführen"'
        ' style="margin-left:10px;padding:1px 10px;border-radius:99px;background:#1b1b1b;color:#ffd43b;text-decoration:none;'
        f'white-space:nowrap">✅ OK → nach Prod ({escape(branch)})</a>')
    # unterdrückte Mails ansehen – Seite nur fuer Admins mit 2FA
    btn = (' <a href="/qa/postfach" title="Mails, die QA sonst verschicken würde (nur Admins)"'
           ' style="margin-left:10px;padding:1px 10px;border-radius:99px;background:#1b1b1b;color:#ffd43b;text-decoration:none;'
           'white-space:nowrap">📬 Postfach</a>') + btn
    band = ('<div class="qa-band" style="position:sticky;top:0;z-index:100000;background:#ffd43b;color:#1b1b1b;'
            'font:600 13px/1.4 system-ui,sans-serif;text-align:center;padding:5px 10px;'
            f'border-bottom:2px solid #e8590c">{escape(txt)}{btn}</div>').encode()
    return body[:m.end()] + band + body[m.end():]


class QaBanner:
    def __init__(self, app, version="", git_ref=""):
        self.app, self.version, self.git_ref = app, version, git_ref

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        state, chunks = {}, []

        async def wrapped(msg):
            if msg["type"] == "http.response.start":
                ctype = dict(msg.get("headers") or []).get(b"content-type", b"")
                if not ctype.startswith(b"text/html"):
                    state["pass"] = True
                    return await send(msg)
                state["head"] = msg
                return
            if state.get("pass") or "head" not in state:
                return await send(msg)
            chunks.append(msg.get("body", b""))
            if msg.get("more_body"):
                return
            body = banner_html(b"".join(chunks), self.version, self.git_ref)
            head = state["head"]
            head["headers"] = [(k, v) for k, v in head.get("headers") or [] if k != b"content-length"]
            head["headers"].append((b"content-length", str(len(body)).encode()))
            await send(head)
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, wrapped)
