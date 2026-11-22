"""QA-Stage (v0.21.0): Band auf HTML-Seiten, keine Mails."""
from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import mailer
import stage


def _client():
    big = "<p>" + "x" * 5000 + "</p>"

    async def page(request):
        return HTMLResponse(f'<!doctype html><html><body class="b">{big}</body></html>')

    async def api(request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/", page), Route("/api", api)])
    app.add_middleware(stage.QaBanner, version="9.9", git_ref="feature/x@abc1234")   # innen
    app.add_middleware(GZipMiddleware, minimum_size=100)   # aussen, wie in main.py
    return TestClient(app)


def test_banner_on_html_also_with_gzip():
    c = _client()
    r = c.get("/", headers={"accept-encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    assert '<body class="b"><div class="qa-band"' in r.text and "Version 9.9 · feature/x@abc1234" in r.text
    assert r.text.endswith("</html>")


def test_no_banner_on_json():
    r = _client().get("/api")
    assert r.json() == {"ok": True}


def test_banner_html_without_body_unchanged():
    assert stage.banner_html(b"<p>kein body</p>", "1") == b"<p>kein body</p>"


def test_mail_off(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda msg: sent.append(msg))
    monkeypatch.setenv("MAIL_OFF", "1")
    assert mailer.send("a@example.org", "Test", "x") is False and sent == []
    monkeypatch.delenv("MAIL_OFF")
    assert mailer.send("a@example.org", "Test", "x") is True and len(sent) == 1


def test_banner_hat_knopf_nach_prod_fuer_branches():
    """0.36.0: „✅ OK → nach Prod“ fuehrt zum Forgejo-Ablauf qa-nach-prod (Ausrollen nur dort, die App hat keine Rechte)."""
    out = stage.banner_html(b"<html><body>x</body></html>", "9.9", "konto-touren@abc1234").decode()
    assert 'href="https://forgejo.example/actions?workflow=qa-nach-prod.yml"' in out
    assert "OK → nach Prod" in out and "konto-touren" in out and 'target="_blank"' in out and 'rel="noopener"' in out
    for ref in ("", "master@abc1234"):
        assert "nach Prod" not in stage.banner_html(b"<body>x</body>", "9.9", ref).decode()


def test_banner_knopf_escaped():
    out = stage.banner_html(b"<body>x</body>", "9.9", '<b>"x@1').decode()
    assert "<b>" not in out.split("<body>")[1].split("</div>")[0].replace("<body>", "")
