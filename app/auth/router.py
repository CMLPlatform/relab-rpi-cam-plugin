"""Authentication routes for browser-session access to the UI."""

import logging
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth.dependencies import (
    SESSION_COOKIE_MAX_AGE_SECONDS,
    _is_authorized,
    create_session,
    delete_session,
    has_valid_session,
    reload_authorized_keys,
    verify_cookie_write_csrf,
)
from app.core.runtime import get_request_runtime
from app.core.settings import settings
from app.observability.logging import build_security_log_extra

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _safe_local_redirect_target(redirect_url: str) -> str:
    """Return a safe local-only redirect target."""
    parsed = urlsplit(redirect_url)
    if parsed.scheme or parsed.netloc:
        return "/"

    path = parsed.path or "/"
    # Browsers normalize backslashes to slashes, so "/\evil.com" becomes the
    # protocol-relative "//evil.com". Reject both leading-slash escapes.
    if not path.startswith("/") or path.startswith(("//", "/\\")):
        return "/"

    return urlunsplit(("", "", path, parsed.query, ""))


@router.post("/login")
async def login(
    request: Request,
    api_key: Annotated[str, Form()],
    redirect_url: Annotated[str, Form()] = "/",
) -> RedirectResponse:
    """Validate an API key and create a browser session."""
    authorized_api_keys = reload_authorized_keys(get_request_runtime(request).runtime_state)
    if not _is_authorized(api_key, authorized_api_keys):
        logger.warning(
            "Security event: browser login failed",
            extra=build_security_log_extra(
                event="auth.login",
                outcome="failure",
                request=request,
                auth_method="api_key",
                status_code=403,
            ),
        )
        raise HTTPException(status_code=403, detail="Invalid API Key")
    delete_session(request.cookies.get(settings.browser_session_cookie_name))
    session_token = create_session()
    safe_redirect_url = _safe_local_redirect_target(redirect_url)
    response = RedirectResponse(url=safe_redirect_url, status_code=303)
    response.set_cookie(
        key=settings.browser_session_cookie_name,
        value=session_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
        path="/",
    )
    logger.info(
        "Security event: browser login succeeded",
        extra=build_security_log_extra(
            event="auth.login",
            outcome="success",
            request=request,
            auth_method="api_key",
            status_code=303,
        ),
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
) -> RedirectResponse:
    """Invalidate the current browser session."""
    session_token = request.cookies.get(settings.browser_session_cookie_name)
    valid_session = has_valid_session(session_token)
    if valid_session:
        verify_cookie_write_csrf(request)
    delete_session(session_token)
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key=settings.browser_session_cookie_name, path="/", secure=settings.cookie_secure)
    response.headers["Clear-Site-Data"] = '"cache", "storage"'
    if valid_session:
        logger.info(
            "Security event: browser logout succeeded",
            extra=build_security_log_extra(
                event="auth.logout",
                outcome="success",
                request=request,
                auth_method="browser_session",
                status_code=303,
            ),
        )
    return response
