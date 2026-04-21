"""Home page router."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from relab_rpi_cam_models.stream import StreamMode

from app.camera.dependencies import CameraManagerDependency
from app.core.settings import settings
from app.core.templates_config import templates

router = APIRouter()


@router.get("/")
async def homepage(request: Request, camera_manager: CameraManagerDependency) -> HTMLResponse:
    """Render homepage."""
    youtube_url: str | None = None
    last_image_url = camera_manager.last_image_url
    if camera_manager.stream.mode == StreamMode.YOUTUBE and camera_manager.stream.url:
        youtube_url = str(camera_manager.stream.url)
    return templates.TemplateResponse(
        request,
        "homepage.html",
        {
            "youtube_url": youtube_url,
            "last_image_url": last_image_url,
        },
    )


@router.get("/favicon.ico")
async def favicon() -> FileResponse:
    """Return the favicon.ico file directly."""
    return FileResponse(settings.static_path / "favicon.ico", media_type="image/x-icon")
