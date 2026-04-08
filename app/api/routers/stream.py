"""Router for video streaming endpoints."""

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import RedirectResponse
from relab_rpi_cam_models.stream import (
    StreamMode,
    StreamView,
    YoutubeConfigRequiredError,
    YoutubeStreamConfig,
)

from app.api.dependencies.camera_management import CameraManagerDependency
from app.api.exceptions import (
    ActiveStreamError,
    YouTubeValidationError,
)

# Initialize router
router = APIRouter(prefix="/stream", tags=["stream"])


@router.post("/start", status_code=201, summary="Start YouTube streaming")
async def start_stream(
    camera_manager: CameraManagerDependency,
    mode: Annotated[StreamMode, Query(description="Streaming mode (youtube)", examples=["youtube"])],
    youtube_config: Annotated[
        YoutubeStreamConfig | None,
        Body(
            description="YouTube stream configuration",
            examples=[{"stream_key": "abc123", "broadcast_key": "def456"}],
        ),
    ] = None,
) -> StreamView:
    """Start YouTube video streaming."""
    try:
        return await camera_manager.start_streaming(mode, youtube_config=youtube_config)

    except YoutubeConfigRequiredError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except YouTubeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ActiveStreamError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/status")
async def get_stream_status(camera_manager: CameraManagerDependency) -> StreamView:
    """Get current stream status."""
    if (stream_info := await camera_manager.get_stream_info()) is None:
        raise HTTPException(404, "No stream active")
    return stream_info


@router.get("")
async def status_redirect() -> RedirectResponse:
    """Redirect to stream status."""
    return RedirectResponse(router.url_path_for("get_stream_status"))


@router.delete("/stop", status_code=204, summary="Stop YouTube streaming")
async def stop_stream(camera_manager: CameraManagerDependency) -> None:
    """Stop active YouTube stream."""
    if not camera_manager.stream.is_active:
        raise HTTPException(404, "No stream active")
    try:
        return await camera_manager.stop_streaming()
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
