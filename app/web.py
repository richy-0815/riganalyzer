"""Anfrage-Helfer des Kerns (seit 0.46.5, Trennung Ride/Rig Schritt 3c): von main und den App-Routern benutzt,
damit die App-Router main nicht importieren muessen."""

import logging
import os

from fastapi import HTTPException, Request

import auth
import twofa

access = logging.getLogger("zugriff")


APPS = ("ride", "rig")


def single_app():
    """Phase 2 (seit 0.47.0): RA_APP=ride|rig -> dieser Container bedient nur diese App. Leer: beide (Host-Verteiler)."""
    a = os.environ.get("RA_APP", "").strip()
    return a if a in APPS else None


def served_apps():
    return (single_app(),) if single_app() else APPS


def app_of_host(host) -> str:
    """Welche App bedient diesen Host? Rig-Hosts beginnen mit "riganalyzer" (auch QA und e2e), alles andere: Ride."""
    return "rig" if (host or "").lower().startswith("riganalyzer") else "ride"


def _host(scope) -> str:
    hdr = dict(scope.get("headers") or [])
    return (hdr.get(b"x-forwarded-host") or hdr.get(b"host") or b"").decode("latin-1")


def app_of_scope(scope) -> str:
    """App einer Anfrage: von der App selbst gesetzt (ProfileScope), sonst aus dem Host-Kopf."""
    if scope.get("ra_app") in APPS:
        return scope["ra_app"]
    return app_of_host(_host(scope))


def is_rig(request: Request) -> bool:
    """Seit 0.46.8 entscheidet nur noch der Host (bzw. der Host-Verteiler), nicht mehr "?app=rig" (Review 13.11.2026)."""
    return app_of_scope(request.scope) == "rig"


class ProfileScope:
    """Aeusserste Schicht jeder App (seit 0.46.8): die App sagt selbst, wer sie ist – unabhaengig vom Host-Kopf."""

    def __init__(self, app, profile):
        self.app, self.profile = app, profile

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope["ra_app"] = self.profile
        await self.app(scope, receive, send)


class HostDispatch:
    """Uebergang Phase 1 -> 2 (Spec): ein Prozess, je App eine eigene FastAPI-App; der Host waehlt die App.
    In Phase 2 startet jeder Container nur noch seine App."""

    def __init__(self, apps, default="ride"):
        self.apps, self.default = apps, default

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            await self.apps.get(app_of_host(_host(scope)), self.apps[self.default])(scope, receive, send)
        else:   # lifespan: die Apps haben keine eigenen Start-/Stopp-Handler (Threads starten beim Import von main)
            await self.apps[self.default](scope, receive, send)


def rig_host(request: Request):
    """Gibt es nur im RigAnalyzer (Trennung Phase 1, 0.46.2): auf dem Ride-Host schlicht nicht vorhanden."""
    if not is_rig(request):
        raise HTTPException(404, "Nicht gefunden.")


def client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "?")


def who(request: Request) -> str:
    """'user@ip': Benutzer aus der Anmeldung (Cookie oder Basic-Auth), IP aus X-Forwarded-For."""
    return f"{getattr(request.state, 'user', None) or '-'}@{client_ip(request)}"


def scope(me):
    """Admin-Bereich des Logins: "all" (Oberadmin), Gruppenliste oder None."""
    return auth.admin_scope(auth.users().get(me or "", {}))


def may_moderate(request: Request, me):
    """Fremde Bewertungen loeschen / fremde Analysen abbrechen (Ticket #16): nur der Oberadmin, per Anmelde-Cookie
    mit bestandener 2FA – nicht Gruppen-Admins (auch nicht fuer Logins ausserhalb ihrer Gruppen), nicht per Basic-Auth."""
    return bool(me and auth.is_admin(me) and request.state.via == "cookie" and twofa.mfa_ok(request, me)
                and scope(me) == "all")
