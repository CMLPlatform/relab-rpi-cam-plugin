"""HTTP middleware: rate limiting, request IDs, security headers, CORS, PNA."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders

from app.core.settings import settings
from app.observability.logging import bind_request_id, new_request_id, reset_request_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from starlette.responses import Response
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

RATE_LIMIT_METHOD = "POST"
RATE_LIMIT_PATH = "/auth/login"

_SETUP_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self' http: https: ws: wss:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src 'self' data: https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_DEFAULT_CSP = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
_HTTPS_SCHEME = "https"
_HTTP_SCOPE_TYPE = "http"
_HTTP_RESPONSE_START = "http.response.start"


class RateLimiter:
    """Simple rate limiter for brute force protection on /auth/login.

    Implemented as a plain helper class. The actual middleware is registered
    with `@app.middleware("http")` to avoid subclass signature/type mismatch
    with Starlette's `BaseHTTPMiddleware.dispatch`.
    """

    MAX_ATTEMPTS = 5
    WINDOW_SIZE = 300
    BLOCK_DURATION = 300
    MAX_TRACKED_IPS = 1000

    def __init__(self) -> None:
        self._attempts: dict[str, list[tuple[float, bool]]] = defaultdict(list)

    def _sweep_stale_entries(self, now: float) -> None:
        """Remove entries with no attempts within the time window."""
        stale_ips = [
            ip for ip, attempts in self._attempts.items() if all(now - ts >= self.WINDOW_SIZE for ts, _ in attempts)
        ]
        for ip in stale_ips:
            del self._attempts[ip]

    async def handle(self, request: Request, call_next: Callable) -> Response:
        """Check rate limits before passing request to the app."""
        if request.method != RATE_LIMIT_METHOD or request.url.path != RATE_LIMIT_PATH:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        now = time.time()
        if client_ip in self._attempts:
            self._attempts[client_ip] = [
                (ts, failed) for ts, failed in self._attempts[client_ip] if now - ts < self.WINDOW_SIZE
            ]

        if len(self._attempts) > self.MAX_TRACKED_IPS:
            self._sweep_stale_entries(now)

        attempts = self._attempts[client_ip]
        if attempts:
            failed_count = sum(1 for _, failed in attempts if failed)
            if failed_count >= self.MAX_ATTEMPTS:
                last_failed_time = max(ts for ts, failed in attempts if failed)
                if now - last_failed_time < self.BLOCK_DURATION:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Too many failed login attempts. Try again later."},
                    )

        response = await call_next(request)

        is_failed = response.status_code >= 400
        self._attempts[client_ip].append((now, is_failed))

        return response


_rate_limiter = RateLimiter()


async def rate_limit_middleware(request: Request, call_next: Callable) -> Response:
    """Apply rate limiting on specific endpoints."""
    return await _rate_limiter.handle(request, call_next)


async def request_context_middleware(request: Request, call_next: Callable) -> Response:
    """Attach a request id to the current context and echo it to the client."""
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    token = bind_request_id(request_id)
    try:
        response = await call_next(request)
    finally:
        reset_request_id(token)

    response.headers["X-Request-ID"] = request_id
    return response


def _content_security_policy_for_path(path: str) -> str:
    """Return the appropriate CSP for the requested route."""
    if path in {"/", "/setup"}:
        return _SETUP_CSP
    if path.startswith("/docs"):
        return _DOCS_CSP
    return _DEFAULT_CSP


async def security_headers_middleware(request: Request, call_next: Callable) -> Response:
    """Attach baseline security headers to every HTTP response."""
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", _content_security_policy_for_path(request.url.path))
    if settings.base_url.scheme == _HTTPS_SCHEME:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


class _PrivateNetworkAccessMiddleware:
    """Add Access-Control-Allow-Private-Network: true to every response.

    Chrome's Private Network Access (PNA) spec requires this header on both
    preflight and regular responses when an HTTPS page fetches resources from a
    private-network host (e.g. a LAN IP).  Without it, once Chrome enforces PNA
    fully, requests from ``https://app.cml-relab.org`` to the Pi's HTTP API will
    be blocked.  Browsers currently warn (``Local Network Access detected``) but
    do not block — this header suppresses the warnings and future-proofs the app.

    Must be registered AFTER CORSMiddleware so it wraps it (last added =
    outermost in Starlette's middleware stack), ensuring the header is appended
    to CORS preflight (OPTIONS) responses as well as regular responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != _HTTP_SCOPE_TYPE or not settings.local_mode_enabled:
            await self.app(scope, receive, send)
            return

        async def _send_with_pna(message: Message) -> None:
            if message["type"] == _HTTP_RESPONSE_START:
                headers = MutableHeaders(scope=message)
                headers.append("Access-Control-Allow-Private-Network", "true")
            await send(message)

        await self.app(scope, receive, _send_with_pna)


def register_middleware(app: FastAPI) -> None:
    """Install the full middleware stack on the FastAPI app."""
    app.middleware("http")(rate_limit_middleware)
    app.middleware("http")(request_context_middleware)
    app.middleware("http")(security_headers_middleware)

    # CORS: wildcard when local_mode is enabled (X-API-Key auth, no cookies),
    # explicit allow-list otherwise (relay-only security model).
    if settings.local_mode_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "PATCH", "PUT"],
            allow_headers=["Content-Type", "Authorization", "Accept", "X-Request-ID", settings.auth_key_name],
        )
    else:
        cors_origins = [str(origin).rstrip("/") for origin in settings.allowed_cors_origins]
        cors_origins += [o.rstrip("/") for o in settings.local_allowed_origins]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "PATCH", "PUT"],
            allow_headers=["Content-Type", "Authorization", "Accept", "X-Request-ID", settings.auth_key_name],
        )

    if settings.local_mode_enabled:
        app.add_middleware(_PrivateNetworkAccessMiddleware)
