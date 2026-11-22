"""Obergrenze fuer Anfrage-Koerper (seit 0.38.0).

Ohne sie las Starlette jedes Formular ganz ein – Dateiteile landen in /tmp, und das ist im Container ein tmpfs
(= RAM). Ein paar GB an /login (ohne Anmeldung erreichbar) haetten den Container ueber sein Speicherlimit getrieben.

Reine ASGI-Middleware: prueft Content-Length vorab und zaehlt beim Lesen mit (chunked/ohne Laenge).
"""

import json


class TooLarge(Exception):
    pass


class BodyLimit:
    def __init__(self, app, default, small, small_paths, large=None, large_prefix=(), large_suffix=()):
        self.app = app
        self.default, self.small, self.small_paths = default, small, frozenset(small_paths)
        self.large, self.large_prefix, self.large_suffix = large or default, tuple(large_prefix), tuple(large_suffix)

    def limit(self, path):
        if path in self.small_paths:
            return self.small
        if self.large_prefix and path.startswith(self.large_prefix) and path.endswith(self.large_suffix):
            return self.large
        return self.default

    async def _reject(self, send, limit):
        body = json.dumps({"detail": f"Anfrage zu groß (max. {limit // 1024 // 1024 or 1} MB)."}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        limit = self.limit(scope["path"])
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > limit:
                        return await self._reject(send, limit)
                except ValueError:
                    return await self._reject(send, limit)
        seen, started = 0, False

        async def rx():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise TooLarge()
            return msg

        async def tx(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, rx, tx)
        except BaseException as e:   # anyio packt Fehler aus Task-Gruppen in ExceptionGroups
            if not _is_too_large(e):
                raise
            if not started:
                await self._reject(send, limit)


def _is_too_large(e):
    if isinstance(e, TooLarge):
        return True
    return any(_is_too_large(x) for x in getattr(e, "exceptions", ()))
