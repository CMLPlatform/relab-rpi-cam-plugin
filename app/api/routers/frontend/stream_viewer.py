"""Router for video streaming endpoints."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from relab_rpi_cam_models.stream import StreamMode

from app.api.dependencies.auth import require_cookie_auth
from app.api.dependencies.camera_management import CameraManagerDependency
from app.core.config import settings

HLS_DIR = settings.hls_path

# Initialize templates
templates = Jinja2Templates(directory=settings.templates_path)

# Initialize router
router = APIRouter(prefix="/stream/watch", tags=["stream"], dependencies=[Depends(require_cookie_auth)])


# YouTube stream viewer endpoint
@router.get("/youtube", summary="Watch YouTube video stream in browser")
async def watch_youtube_stream(request: Request) -> HTMLResponse:
    """Render the YouTube stream viewer template."""
    return templates.TemplateResponse(
        "youtube_stream_viewer.html",
        {"request": request},
    )


# Local stream viewer endpoint
@router.get("/local", summary="Watch local video stream in browser")
async def watch_local_stream(request: Request) -> HTMLResponse:
    """Render the local stream viewer template."""
    return templates.TemplateResponse("local_stream_viewer.html", {"request": request})


# Main redirect endpoint
@router.get("", summary="Redirect to appropriate stream viewer")
async def redirect_stream_viewer(camera_manager: CameraManagerDependency) -> RedirectResponse:
    """Redirect to the correct stream viewer endpoint based on stream mode."""
    if camera_manager.stream.mode == StreamMode.YOUTUBE:
        return RedirectResponse(url=router.url_path_for("watch_youtube_stream"), status_code=303)
    return RedirectResponse(url=router.url_path_for("watch_local_stream"), status_code=303)
