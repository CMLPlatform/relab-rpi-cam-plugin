"""Authorization dependencies for FastAPI."""

import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyCookie, APIKeyHeader

from app.core.config import settings

# TODO: Improve API key handling
#  - Add API key management endpoints in the Raspberry Pi API and the main API
#  - Add API key expiration and automated rotation
#  - Add automated key syncing between main API and Raspberry Pi
#  - Consider just using Oauth2 with JWT tokens


api_key_header = APIKeyHeader(
    name=settings.auth_key_name, auto_error=False, description="API Key from user of the main API."
)
api_key_cookie = APIKeyCookie(
    name=settings.auth_key_name, auto_error=False, description="API Key from user of the main API."
)


def _hash_key(key: str) -> str:
    """Return a hex-encoded SHA-256 hash of the given key."""
    return hashlib.sha256(key.encode()).hexdigest()


def _is_authorized(api_key: str) -> bool:
    """Check if an API key matches any authorized key using timing-safe comparison."""
    incoming_hash = _hash_key(api_key)
    return any(hmac.compare_digest(incoming_hash, _hash_key(stored)) for stored in settings.authorized_api_keys)


async def verify_request(
    x_api_key_header: Annotated[str | None, Security(api_key_header)] = None,
    # NOTE: We use Depends and not Security for the cookie because openapi does not work with cookie-based auth: https://github.com/swagger-api/swagger-js/issues/1163
    x_api_key_cookie: Annotated[str | None, Depends(api_key_cookie)] = None,
) -> str:
    """General request verification checking API key in header or cookie."""
    api_key = x_api_key_header or x_api_key_cookie

    if not api_key:
        raise HTTPException(status_code=401, detail="API Key header or cookie is missing")
    if not _is_authorized(api_key):
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key


async def require_cookie_auth(request: Request) -> bool:
    """Check if user is authenticated via cookie, redirect to login if not."""
    cookie_value = request.cookies.get(settings.auth_key_name)
    if not cookie_value or not _is_authorized(cookie_value):
        current_path = str(request.url.path)
        login_url = f"/login?redirect_url={current_path}"
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": login_url})
    return True


async def get_auth_status(request: Request) -> bool:
    """Get authentication status without redirecting (for templates)."""
    cookie_value = request.cookies.get(settings.auth_key_name)
    return bool(cookie_value and _is_authorized(cookie_value))
