"""Home page router."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from relab_rpi_cam_models.stream import StreamMode

from app.camera.dependencies import CameraManagerDependency
from app.core.settings import settings
from app.core.templates_config import templates
from app.utils.network import is_local_client

router = APIRouter()


@router.get("/")
async def homepage(request: Request, camera_manager: CameraManagerDependency) -> HTMLResponse:
    """Render homepage."""
    youtube_url: str | None = None
    if camera_manager.stream.mode == StreamMode.YOUTUBE and camera_manager.stream.url:
        youtube_url = str(camera_manager.stream.url)
    return templates.TemplateResponse(
        request,
        "homepage.html",
        {"youtube_url": youtube_url},
    )


@router.get("/favicon.ico")
async def favicon() -> FileResponse:
    """Return the favicon.ico file directly."""
    return FileResponse(settings.static_path / "favicon.ico", media_type="image/x-icon")


@router.get("/preview-thumbnail.jpg", include_in_schema=False)
async def preview_thumbnail(request: Request) -> FileResponse:
    """Serve the cached preview thumbnail written by PreviewThumbnailWorker."""
    if not is_local_client(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="Preview thumbnail is only available from the local network")
    path = settings.image_path / "preview-thumbnail" / "current.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No preview thumbnail cached yet")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
