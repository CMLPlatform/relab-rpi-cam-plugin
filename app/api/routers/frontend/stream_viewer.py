"""Router for video streaming endpoints."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.dependencies.auth import require_cookie_auth
from app.api.dependencies.camera_management import CameraManagerDependency
from app.core.templates_config import templates

# Initialize router
router = APIRouter(prefix="/stream/watch", tags=["stream"], dependencies=[Depends(require_cookie_auth)])


@router.get("/youtube", summary="Watch YouTube video stream in browser")
async def watch_youtube_stream(request: Request, camera_manager: CameraManagerDependency) -> HTMLResponse:
    """Render the YouTube stream viewer template."""
    broadcast_key = (
        camera_manager.stream.youtube_config.broadcast_key.get_secret_value()
        if camera_manager.stream.youtube_config
        else ""
    )
    return templates.TemplateResponse(request, "youtube_stream_viewer.html", {"broadcast_key": broadcast_key})


@router.get("", summary="Redirect to YouTube stream viewer")
async def redirect_stream_viewer() -> RedirectResponse:
    """Redirect to the YouTube stream viewer."""
    return RedirectResponse(url=router.url_path_for("watch_youtube_stream"), status_code=303)
