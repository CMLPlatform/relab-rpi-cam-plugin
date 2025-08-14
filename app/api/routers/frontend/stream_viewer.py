"""Router for video streaming endpoints."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.dependencies.camera_management import CameraManagerDependency
from app.api.routers.frontend.auth import router as auth_router
from app.core.config import settings
from relab_rpi_cam_models.stream import StreamMode

HLS_DIR = settings.hls_path

# Initialize templates
templates = Jinja2Templates(directory=settings.templates_path)

# Initialize router
router = APIRouter(prefix="/stream/watch", tags=["stream"])


@router.get("", summary="Watch video stream in browser")
async def watch_stream(request: Request, camera_manager: CameraManagerDependency) -> HTMLResponse | RedirectResponse:
    """Redirect to appropriate stream viewer based on active stream."""
    # Check if user is logged in
    logged_in = bool(request.cookies.get(settings.auth_key_name))
    if not logged_in:
        return RedirectResponse(
            url=f"{auth_router.url_path_for('login_form')}?redirect_url=/stream/watch", status_code=303
        )

    if camera_manager.stream.mode == StreamMode.YOUTUBE:
        if not camera_manager.stream.youtube_config:
            raise HTTPException(400, "No broadcast key provided for YouTube stream")
        return templates.TemplateResponse(
            "youtube_stream_viewer.html",
            {
                "request": request,
                "logged_in": logged_in,
                "broadcast_key": camera_manager.stream.youtube_config.broadcast_key,
            },
        )

    # Default to local stream viewer if no stream active
    response = templates.TemplateResponse("local_stream_viewer.html", {"request": request, "logged_in": logged_in})

    return response
