"""Authentication routes for cookie-based access to the API."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from relab_rpi_cam_plugin.core.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_form(redirect_url: Annotated[str, Query(alias="redirect_url")] = "/") -> str:
    """Render the login form."""
    return f"""
    <form action="/auth/login" method="post">
        <input name="api_key" type="password" placeholder="API Key" required>
        <input type="hidden" name="redirect_url" value="{redirect_url}">
        <button type="submit">Login</button>
    </form>
    """


@router.post("/login")
async def login(
    response: Response, api_key: Annotated[str, Form()], redirect_url: Annotated[str, Form()] = "/"
) -> RedirectResponse:
    """Cookie-based login."""
    if api_key not in settings.authorized_api_keys:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    response = RedirectResponse(url=redirect_url, status_code=303)
    response.set_cookie(key="X-API-Key", value=api_key, httponly=True, secure=True, samesite="lax")
    return response


@router.get("/logout")
async def logout(response: Response) -> RedirectResponse:
    """Handle cookie-based logout."""
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key=settings.auth_key_name, path="/")
    return response
