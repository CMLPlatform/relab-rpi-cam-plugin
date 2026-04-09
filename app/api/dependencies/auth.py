"""Authorization dependencies for FastAPI."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import settings

# TODO: Improve API key handling
#  - Add API key management endpoints in the Raspberry Pi API and the main API
#  - Add API key expiration and automated rotation
#  - Add automated key syncing between main API and Raspberry Pi
#  - Consider just using Oauth2 with JWT tokens


SESSION_TTL_HOURS = 12

api_key_header = APIKeyHeader(name=settings.auth_key_name, auto_error=False, description="API Key for API access.")

_active_sessions: dict[str, datetime] = {}


def _hash_key(key: str) -> str:
    """Return a hex-encoded SHA-256 hash of the given key."""
    return hashlib.sha256(key.encode()).hexdigest()


def _get_authorized_hashes() -> list[str]:
    """Pre-hash all authorized keys for efficient comparison."""
    return [_hash_key(k) for k in settings.authorized_api_keys]


# Pre-compute hashes at import time; refreshed when keys change via reload_authorized_hashes().
_authorized_hashes: list[str] = _get_authorized_hashes()


def reload_authorized_hashes() -> None:
    """Recompute the cached key hashes (call after modifying authorized_api_keys)."""
    global _authorized_hashes  # noqa: PLW0603
    _authorized_hashes = _get_authorized_hashes()


def _is_authorized(api_key: str) -> bool:
    """Check if an API key matches any authorized key using timing-safe comparison."""
    incoming_hash = _hash_key(api_key)
    return any(hmac.compare_digest(incoming_hash, stored_hash) for stored_hash in _authorized_hashes)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _purge_expired_sessions(now: datetime | None = None) -> None:
    """Drop expired browser sessions from the in-memory session store."""
    current_time = now or _now_utc()
    expired_tokens = [token for token, expires_at in _active_sessions.items() if expires_at <= current_time]
    for token in expired_tokens:
        del _active_sessions[token]


def create_session() -> str:
    """Create and register a new browser session token."""
    _purge_expired_sessions()
    token = secrets.token_urlsafe(32)
    _active_sessions[token] = _now_utc() + timedelta(hours=SESSION_TTL_HOURS)
    return token


def delete_session(token: str | None) -> None:
    """Invalidate a browser session token if present."""
    if token:
        _active_sessions.pop(token, None)


def has_valid_session(token: str | None) -> bool:
    """Return whether the given browser session token is currently valid."""
    if not token:
        return False
    _purge_expired_sessions()
    return token in _active_sessions


async def verify_request(
    request: Request,
    x_api_key_header: Annotated[str | None, Security(api_key_header)] = None,
) -> str:
    """Verify API access using a valid API key header or browser session."""
    if x_api_key_header:
        if not _is_authorized(x_api_key_header):
            raise HTTPException(status_code=403, detail="Invalid API Key")
        return x_api_key_header

    session_token = request.cookies.get(settings.session_cookie_name)
    if has_valid_session(session_token):
        return "browser-session"

    raise HTTPException(status_code=401, detail="API Key header or browser session is missing")


async def require_cookie_auth(request: Request) -> bool:
    """Check if user has a valid browser session cookie, redirect to login if not."""
    session_token = request.cookies.get(settings.session_cookie_name)
    if not has_valid_session(session_token):
        current_path = str(request.url.path)
        login_url = f"/login?redirect_url={current_path}"
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": login_url})
    return True


async def get_auth_status(request: Request) -> bool:
    """Return whether the request carries a valid browser session."""
    return has_valid_session(request.cookies.get(settings.session_cookie_name))
