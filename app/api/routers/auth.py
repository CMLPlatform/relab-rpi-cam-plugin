"""Authentication routes for browser-session access to the UI."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.api.dependencies.auth import _is_authorized, create_session, delete_session
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(
    response: Response, api_key: Annotated[str, Form()], redirect_url: Annotated[str, Form()] = "/"
) -> RedirectResponse:
    """Validate an API key and create a browser session."""
    if not _is_authorized(api_key):
        raise HTTPException(status_code=403, detail="Invalid API Key")
    # Validate redirect_url is a relative path to prevent open redirect attacks.
    # Reject protocol-relative URLs like //evil.com which start with "/" but
    # are treated as external redirects by browsers.
    if not redirect_url.startswith("/") or redirect_url.startswith("//"):
        redirect_url = "/"
    session_token = create_session()
    response = RedirectResponse(url=redirect_url, status_code=303)
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
