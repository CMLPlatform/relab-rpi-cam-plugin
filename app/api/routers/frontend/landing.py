"""Home page and login form router."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from app.api.dependencies.auth import get_auth_status
from app.core.config import settings
from app.core.templates_config import templates

router = APIRouter()


@router.get("/")
async def homepage(request: Request, logged_in: Annotated[bool, Depends(get_auth_status)]) -> HTMLResponse:
    """Render homepage."""
    return templates.TemplateResponse(request, "homepage.html", {"logged_in": logged_in})


@router.get("/login")
async def login_form(request: Request, redirect_url: Annotated[str, Query(alias="redirect_url")] = "/") -> HTMLResponse:
    """Render the login form."""
    return templates.TemplateResponse(request, "login.html", {"redirect_url": redirect_url})


@router.get("/favicon.ico")
async def favicon() -> FileResponse:
    """Return the favicon.ico file directly."""
    return FileResponse(settings.static_path / "favicon.ico", media_type="image/x-icon")
