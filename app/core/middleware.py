"""HTTP middleware: rate limiting, request IDs, security headers, and CORS."""

from __future__ import annotations

import logging
import re
import time
from hashlib import sha256
from secrets import token_urlsafe
from typing import TYPE_CHECKING, NamedTuple

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.settings import settings
from app.observability.logging import bind_request_id, build_security_log_extra, new_request_id, reset_request_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from starlette.responses import Response

_HOMEPAGE_PATH = "/"
_SETUP_PATH = "/setup"
_DOCS_PATH_PREFIX = "/docs"
_LOGIN_ROUTE = ("POST", "/auth/login")
_HTML_CONTENT_TYPE = "text/html"
_CSP_NONCE_BYTES = 16
_HEADER_CONTENT_SECURITY_POLICY = "Content-Security-Policy"
_HEADER_CONTENT_TYPE_OPTIONS = "X-Content-Type-Options"
_HEADER_CROSS_ORIGIN_OPENER_POLICY = "Cross-Origin-Opener-Policy"
_HEADER_CROSS_ORIGIN_RESOURCE_POLICY = "Cross-Origin-Resource-Policy"
_HEADER_REFERRER_POLICY = "Referrer-Policy"
_HEADER_REQUEST_ID = "X-Request-ID"
_HEADER_STRICT_TRANSPORT_SECURITY = "Strict-Transport-Security"
_HEADER_CONTENT_TYPE = "content-type"
_VALUE_HSTS = "max-age=31536000; includeSubDomains"
_VALUE_NO_REFERRER = "no-referrer"
_VALUE_NOSNIFF = "nosniff"
_VALUE_SAME_ORIGIN = "same-origin"

_HOMEPAGE_CSP = (
    "default-src 'self'; "
    "script-src 'self'{script_nonce} https://cdn.jsdelivr.net; "
    "worker-src 'self' blob:; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_SETUP_CSP = (
    "default-src 'self'; "
    "script-src 'self'{script_nonce}; "
    "worker-src 'self' blob:; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
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
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_DEFAULT_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
logger = logging.getLogger(__name__)


class _RateLimitPolicy(NamedTuple):
    max_attempts: int
    failed_only: bool


_LOGIN_MAX_FAILED_ATTEMPTS = 5
_ACTION_MAX_ATTEMPTS = 20
_RATE_LIMIT_POLICIES: dict[tuple[str, str], _RateLimitPolicy] = {
    _LOGIN_ROUTE: _RateLimitPolicy(max_attempts=_LOGIN_MAX_FAILED_ATTEMPTS, failed_only=True),
    ("DELETE", "/pairing"): _RateLimitPolicy(max_attempts=_ACTION_MAX_ATTEMPTS, failed_only=False),
    ("PATCH", "/camera/controls"): _RateLimitPolicy(max_attempts=_ACTION_MAX_ATTEMPTS, failed_only=False),
    ("POST", "/captures"): _RateLimitPolicy(max_attempts=_ACTION_MAX_ATTEMPTS, failed_only=False),
    ("POST", "/pairing/code"): _RateLimitPolicy(max_attempts=_ACTION_MAX_ATTEMPTS, failed_only=False),
    ("POST", "/preview/start"): _RateLimitPolicy(max_attempts=_ACTION_MAX_ATTEMPTS, failed_only=False),
    ("POST", "/preview/stop"): _RateLimitPolicy(max_attempts=_ACTION_MAX_ATTEMPTS, failed_only=False),
    ("POST", "/streams/youtube"): _RateLimitPolicy(max_attempts=_ACTION_MAX_ATTEMPTS, failed_only=False),
    ("PUT", "/camera/focus"): _RateLimitPolicy(max_attempts=_ACTION_MAX_ATTEMPTS, failed_only=False),
}


class RateLimiter:
    """Simple in-memory rate limiter for sensitive local-device actions.

    Implemented as a plain helper class. The actual middleware is registered
    with `@app.middleware("http")` to avoid subclass signature/type mismatch
    with Starlette's `BaseHTTPMiddleware.dispatch`.
    """

    LOGIN_MAX_FAILED_ATTEMPTS = _LOGIN_MAX_FAILED_ATTEMPTS
    ACTION_MAX_ATTEMPTS = _ACTION_MAX_ATTEMPTS
    WINDOW_SIZE = 300
    MAX_TRACKED_BUCKETS = 1000

    def __init__(self) -> None:
        self._attempts: dict[str, list[tuple[float, bool]]] = {}

    def _sweep_stale_entries(self, now: float) -> None:
        """Remove entries with no attempts within the time window."""
        stale_keys = [
            key for key, attempts in self._attempts.items() if all(now - ts >= self.WINDOW_SIZE for ts, _ in attempts)
        ]
        for key in stale_keys:
            del self._attempts[key]

    async def handle(self, request: Request, call_next: Callable) -> Response:
        """Check rate limits before passing request to the app."""
        route = (request.method, request.url.path)
        policy = _RATE_LIMIT_POLICIES.get(route)
        if policy is None:
            return await call_next(request)
        bucket_key = self._bucket_key(request, route=route)
        now = time.monotonic()
        attempts = self._fresh_attempts(bucket_key, now)
        counted_attempts = sum(1 for _, failed in attempts if failed) if policy.failed_only else len(attempts)
        if counted_attempts >= policy.max_attempts:
            logger.warning(
                "Security event: rate limit blocked request",
                extra=build_security_log_extra(
                    event="security.rate_limit",
                    outcome="blocked",
                    request=request,
                    status_code=429,
                ),
            )
            return _rate_limit_response()

        response = await call_next(request)
        self._record(bucket_key, now, failed=response.status_code >= 400)
        return response

    def _fresh_attempts(self, bucket_key: str, now: float) -> list[tuple[float, bool]]:
        """Return and persist attempts still inside the fixed window."""
        attempts = [(ts, failed) for ts, failed in self._attempts.get(bucket_key, []) if now - ts < self.WINDOW_SIZE]
        if attempts:
            self._attempts[bucket_key] = attempts
        else:
            self._attempts.pop(bucket_key, None)
        if len(self._attempts) > self.MAX_TRACKED_BUCKETS:
            self._sweep_stale_entries(now)
        return attempts

    def _record(self, bucket_key: str, now: float, *, failed: bool) -> None:
        self._attempts.setdefault(bucket_key, []).append((now, failed))

    def _bucket_key(self, request: Request, *, route: tuple[str, str]) -> str:
        """Return a stable bucket key without storing raw credentials."""
        route_key = ":".join(route)
        if api_key := request.headers.get(settings.auth_key_name):
            return _hashed_bucket(f"{route_key}:api", api_key)
        if session_token := request.cookies.get(settings.browser_session_cookie_name):
            return _hashed_bucket(f"{route_key}:session", session_token)
        client_ip = request.client.host if request.client else "unknown"
        return _hashed_bucket(f"{route_key}:ip", client_ip)


def _hashed_bucket(prefix: str, value: str) -> str:
    return f"{prefix}:{sha256(value.encode()).hexdigest()}"


def _rate_limit_response() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Try again later."},
    )


_rate_limiter = RateLimiter()


async def rate_limit_middleware(request: Request, call_next: Callable) -> Response:
    """Apply rate limiting on specific endpoints."""
    return await _rate_limiter.handle(request, call_next)


async def request_context_middleware(request: Request, call_next: Callable) -> Response:
    """Attach a request id to the current context and echo it to the client."""
    request_id = _request_id_from_header(request.headers.get("X-Request-ID"))
    request.state.request_id = request_id
    token = bind_request_id(request_id)
    try:
        response = await call_next(request)
    finally:
        reset_request_id(token)

    response.headers["X-Request-ID"] = request_id
    return response


def _request_id_from_header(value: str | None) -> str:
    """Return a safe request id, replacing invalid client-supplied values."""
    if value and _REQUEST_ID_RE.fullmatch(value):
        return value
    return new_request_id()


def _csp_for_request_path(path: str, *, nonce: str | None = None) -> str:
    """Return the appropriate CSP for the requested route."""
    script_nonce = f" 'nonce-{nonce}'" if nonce else ""
    if path == _HOMEPAGE_PATH:
        return _HOMEPAGE_CSP.format(script_nonce=script_nonce)
    if path == _SETUP_PATH:
        return _SETUP_CSP.format(script_nonce=script_nonce)
    if path.startswith(_DOCS_PATH_PREFIX):
        return _DOCS_CSP
    return _DEFAULT_CSP


def _is_html_response(response: Response) -> bool:
    return response.headers.get(_HEADER_CONTENT_TYPE, "").lower().startswith(_HTML_CONTENT_TYPE)


async def security_headers_middleware(request: Request, call_next: Callable) -> Response:
    """Attach baseline security headers to every HTTP response."""
    request.state.csp_nonce = token_urlsafe(_CSP_NONCE_BYTES)
    response = await call_next(request)
    response.headers.setdefault(_HEADER_CONTENT_TYPE_OPTIONS, _VALUE_NOSNIFF)
    response.headers.setdefault(_HEADER_REFERRER_POLICY, _VALUE_NO_REFERRER)
    response.headers.setdefault(_HEADER_CROSS_ORIGIN_RESOURCE_POLICY, _VALUE_SAME_ORIGIN)
    if _is_html_response(response):
        response.headers.setdefault(_HEADER_CROSS_ORIGIN_OPENER_POLICY, _VALUE_SAME_ORIGIN)
    response.headers.setdefault(
        _HEADER_CONTENT_SECURITY_POLICY,
        _csp_for_request_path(request.url.path, nonce=request.state.csp_nonce),
    )
    if settings.base_url.scheme == _HTTPS_SCHEME:
        response.headers.setdefault(_HEADER_STRICT_TRANSPORT_SECURITY, _VALUE_HSTS)
    return response


def register_middleware(app: FastAPI) -> None:
    """Install the full middleware stack on the FastAPI app."""
    app.middleware("http")(rate_limit_middleware)
    app.middleware("http")(request_context_middleware)
    app.middleware("http")(security_headers_middleware)

    cors_origins = [str(origin).rstrip("/") for origin in settings.allowed_cors_origins]
    cors_origins += [str(origin).rstrip("/") for origin in settings.local_allowed_origins]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=not settings.local_mode_enabled,
        allow_methods=["GET", "POST", "DELETE", "PATCH", "PUT"],
        allow_headers=["Content-Type", "Authorization", "Accept", "X-Request-ID", settings.auth_key_name],
        allow_private_network=settings.local_mode_enabled,
    )
