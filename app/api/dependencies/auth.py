"""Authorization dependencies for FastAPI."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyCookie, APIKeyHeader

from app.core.config import settings

# TODO: Improve API key handling
#  - Use hashed keys instead of encryption
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


async def verify_request(
    x_api_key_header: Annotated[str | None, Security(api_key_header)] = None,
    # NOTE: We use Depends and not Security for the cookie because openapi does not work with cookie-based auth: https://github.com/swagger-api/swagger-js/issues/1163
    x_api_key_cookie: Annotated[str | None, Depends(api_key_cookie)] = None,
) -> str:
    """General request verification checking API key in header or cookie."""
    api_key = x_api_key_header or x_api_key_cookie

    if not api_key:
        raise HTTPException(status_code=401, detail="API Key header or cookie is missing")
    if api_key not in settings.authorized_api_keys:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key


async def require_cookie_auth(request: Request) -> bool:
    """Check if user is authenticated via cookie, redirect to login if not."""
    logged_in = bool(request.cookies.get(settings.auth_key_name))
    if not logged_in:
        current_path = str(request.url.path)
        login_url = f"/login?redirect_url={current_path}"
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": login_url})
    return logged_in


async def get_auth_status(request: Request) -> bool:
    """Get authentication status without redirecting (for templates)."""
    return bool(request.cookies.get(settings.auth_key_name))
