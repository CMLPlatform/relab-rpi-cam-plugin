"""Authorization dependencies for FastAPI."""

import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.runtime import get_active_runtime, get_request_runtime
from app.core.runtime_state import RuntimeState
from app.core.settings import settings
from app.observability.logging import build_security_log_extra

SESSION_INACTIVITY_TIMEOUT = timedelta(minutes=30)
SESSION_ABSOLUTE_LIFETIME = timedelta(hours=12)
SESSION_COOKIE_MAX_AGE_SECONDS = int(SESSION_ABSOLUTE_LIFETIME.total_seconds())
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SCHEME_PORTS: dict[str, int] = {"http": 80, "https": 443}
logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name=settings.auth_key_name, auto_error=False, description="API Key for API access.")


@dataclass(slots=True)
class BrowserSession:
    """Server-side browser session state."""

    created_at: datetime
    last_seen_at: datetime


_active_sessions: dict[str, BrowserSession] = {}


def reload_authorized_hashes(runtime_state: RuntimeState | None = None) -> frozenset[str]:
    """Return the current immutable authorized-key snapshot used by auth checks."""
    active_state = runtime_state or get_active_runtime().runtime_state
    return frozenset(active_state.authorized_api_keys)


def _is_authorized(api_key: str, authorized_api_keys: frozenset[str]) -> bool:
    """Check if an API key matches any authorized key using timing-safe comparison.

    Readers use an immutable snapshot captured from runtime state for the
    current request or explicit caller context.
    """
    matched = False
    for candidate in authorized_api_keys:
        if hmac.compare_digest(api_key, candidate):
            matched = True
    return matched


def _session_expired(session: BrowserSession, now: datetime) -> bool:
    inactive_for = now - session.last_seen_at
    alive_for = now - session.created_at
    return inactive_for > SESSION_INACTIVITY_TIMEOUT or alive_for > SESSION_ABSOLUTE_LIFETIME


def _purge_expired_sessions(now: datetime | None = None) -> None:
    """Drop expired browser sessions from the in-memory session store."""
    current_time = now or datetime.now(UTC)
    expired_tokens = [token for token, session in _active_sessions.items() if _session_expired(session, current_time)]
    for token in expired_tokens:
        del _active_sessions[token]


def create_session() -> str:
    """Create and register a new browser session token."""
    now = datetime.now(UTC)
    _purge_expired_sessions(now)
    token = secrets.token_urlsafe(32)
    _active_sessions[token] = BrowserSession(created_at=now, last_seen_at=now)
    return token


def delete_session(token: str | None) -> None:
    """Invalidate a browser session token if present."""
    if token:
        _active_sessions.pop(token, None)


def has_valid_session(token: str | None) -> bool:
    """Return whether the given browser session token is currently valid."""
    if not token:
        return False
    now = datetime.now(UTC)
    session = _active_sessions.get(token)
    if session is None:
        return False
    if _session_expired(session, now):
        del _active_sessions[token]
        return False
    session.last_seen_at = now
    return True


def has_valid_browser_session(request: Request) -> bool:
    """Return whether the request carries a valid browser session cookie."""
    return has_valid_session(request.cookies.get(settings.browser_session_cookie_name))


def _same_origin(left: str, right: str) -> bool:
    """Return whether two absolute URLs share scheme, host, and port."""
    try:
        left_parsed = urlsplit(left)
        right_parsed = urlsplit(right)
        if not left_parsed.scheme or not left_parsed.hostname:
            return False
        if not right_parsed.scheme or not right_parsed.hostname:
            return False
        left_port = left_parsed.port or _SCHEME_PORTS.get(left_parsed.scheme)
        right_port = right_parsed.port or _SCHEME_PORTS.get(right_parsed.scheme)
    except ValueError:
        return False
    return (
        left_parsed.scheme == right_parsed.scheme
        and left_parsed.hostname == right_parsed.hostname
        and left_port == right_port
    )


def verify_cookie_write_csrf(request: Request) -> None:
    """Require same-origin browser signals for cookie-authenticated unsafe writes."""
    if request.method.upper() in SAFE_METHODS:
        return

    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin := request.headers.get("Origin"):
        if _same_origin(origin, expected_origin):
            return
    elif (referer := request.headers.get("Referer")) and _same_origin(referer, expected_origin):
        return
    logger.warning(
        "Security event: same-origin browser proof failed",
        extra=build_security_log_extra(
            event="auth.csrf",
            outcome="failure",
            request=request,
            auth_method="browser_session",
            status_code=403,
        ),
    )
    raise HTTPException(status_code=403, detail="Same-origin browser request required")


async def verify_request(
    request: Request,
    x_api_key_header: Annotated[str | None, Security(api_key_header)] = None,
) -> str:
    """Verify API access using a valid API key header or browser session."""
    authorized_api_keys = reload_authorized_hashes(get_request_runtime(request).runtime_state)
    if x_api_key_header:
        if not _is_authorized(x_api_key_header, authorized_api_keys):
            logger.warning(
                "Security event: API-key authentication failed",
                extra=build_security_log_extra(
                    event="auth.request",
                    outcome="failure",
                    request=request,
                    auth_method="api_key",
                    status_code=403,
                ),
            )
            raise HTTPException(status_code=403, detail="Invalid API Key")
        return x_api_key_header

    if has_valid_browser_session(request):
        verify_cookie_write_csrf(request)
        return "browser-session"

    logger.warning(
        "Security event: request authentication missing",
        extra=build_security_log_extra(
            event="auth.request",
            outcome="failure",
            request=request,
            auth_method="missing",
            status_code=401,
        ),
    )
    raise HTTPException(status_code=401, detail="API Key header or browser session is missing")
