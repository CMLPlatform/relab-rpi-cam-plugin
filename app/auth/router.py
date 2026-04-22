"""Authentication routes for browser-session access to the UI."""

from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.auth.dependencies import _is_authorized, create_session, delete_session, reload_authorized_hashes
from app.core.runtime import get_request_runtime
from app.core.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _safe_local_redirect_target(redirect_url: str) -> str:
    """Return a safe local-only redirect target."""
    parsed = urlsplit(redirect_url)
    if parsed.scheme or parsed.netloc:
        return "/"

    path = parsed.path or "/"
    if not path.startswith("/"):
        return "/"

    return urlunsplit(("", "", path, parsed.query, ""))


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    api_key: Annotated[str, Form()],
    redirect_url: Annotated[str, Form()] = "/",
) -> RedirectResponse:
    """Validate an API key and create a browser session."""
    authorized_api_keys = reload_authorized_hashes(get_request_runtime(request).runtime_state)
    if not _is_authorized(api_key, authorized_api_keys):
        raise HTTPException(status_code=403, detail="Invalid API Key")
    session_token = create_session()
    safe_redirect_url = _safe_local_redirect_target(redirect_url)
    response = RedirectResponse(url=safe_redirect_url, status_code=303)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=60 * 60 * 12,
        path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request, response: Response) -> RedirectResponse:
    """Invalidate the current browser session."""
    delete_session(request.cookies.get(settings.session_cookie_name))
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return response
