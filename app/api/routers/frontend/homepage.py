"""Homepage router."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings

HLS_DIR = settings.hls_path

# Initialize templates
templates = Jinja2Templates(directory=settings.templates_path)

router = APIRouter()


@router.get("/")
async def homepage(request: Request) -> HTMLResponse:
    """Render homepage."""
    logged_in = bool(request.cookies.get(settings.auth_key_name))
    response = templates.TemplateResponse("homepage.html", {"request": request, "logged_in": logged_in})
    return response
