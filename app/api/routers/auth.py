"""Authentication routes for cookie-based access to the API."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Response
from fastapi.responses import RedirectResponse

from app.api.dependencies.auth import _is_authorized
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(
    response: Response, api_key: Annotated[str, Form()], redirect_url: Annotated[str, Form()] = "/"
) -> RedirectResponse:
    """Cookie-based login."""
    if not _is_authorized(api_key):
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
