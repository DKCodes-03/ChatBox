"""ASGI middleware for body limits, same-origin mutations, and no-store responses."""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import SafeErrorCode, safe_error_response

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class RequestBodyLimitMiddleware:
    """Reject oversized bodies before routing and without logging their contents."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        lengths = headers.getlist("content-length")
        if len(lengths) > 1:
            await safe_error_response(SafeErrorCode.BAD_REQUEST)(scope, receive, send)
            return
        if lengths:
            try:
                content_length = int(lengths[0])
            except ValueError:
                await safe_error_response(SafeErrorCode.BAD_REQUEST)(scope, receive, send)
                return
            if content_length < 0:
                await safe_error_response(SafeErrorCode.BAD_REQUEST)(scope, receive, send)
                return
            if content_length > self.max_body_bytes:
                await safe_error_response(SafeErrorCode.REQUEST_TOO_LARGE)(scope, receive, send)
                return

        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_body_bytes:
                await safe_error_response(SafeErrorCode.REQUEST_TOO_LARGE)(scope, receive, send)
                return
            buffered.append(message)
            if not message.get("more_body", False):
                break

        async def replay_body() -> Message:
            if buffered:
                return buffered.pop(0)
            return await receive()

        await self.app(scope, replay_body, send)


class OriginValidationMiddleware:
    """Require the configured same origin for every state-changing HTTP request."""

    def __init__(self, app: ASGIApp, *, allowed_origin: str) -> None:
        self.app = app
        self.allowed_origin = allowed_origin.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"].upper() in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        origins = Headers(scope=scope).getlist("origin")
        if len(origins) != 1 or origins[0].rstrip("/") != self.allowed_origin:
            await safe_error_response(SafeErrorCode.ORIGIN_NOT_ALLOWED)(scope, receive, send)
            return
        await self.app(scope, receive, send)


class NoStoreHeadersMiddleware:
    """Prevent browsers and intermediaries from caching any API response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, private"
                headers["Pragma"] = "no-cache"
                headers["Expires"] = "0"
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        await self.app(scope, receive, add_headers)
