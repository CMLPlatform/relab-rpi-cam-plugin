"""Router for video streaming endpoints."""

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException
from relab_rpi_cam_models.stream import StreamMode, StreamView

from app.api.dependencies.camera_management import CameraManagerDependency
from app.api.exceptions import ActiveStreamError, YouTubeValidationError
from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig

router = APIRouter(prefix="/stream", tags=["stream"])


@router.post(
    "",
    status_code=201,
    summary="Start YouTube streaming",
    responses={
        201: {"description": "YouTube stream started successfully."},
        400: {"description": "YouTube config is missing or invalid."},
        409: {"description": "A stream is already active."},
    },
)
async def start_stream(
    camera_manager: CameraManagerDependency,
    youtube_config: Annotated[
        YoutubeStreamConfig,
        Body(
            examples=[{"stream_key": "your-stream-key", "broadcast_key": "your-broadcast-id"}],
        ),
    ],
) -> StreamView:
    """Start a YouTube stream using the provided stream and broadcast keys."""
    try:
        return await camera_manager.start_streaming(StreamMode.YOUTUBE, youtube_config=youtube_config)

    except YoutubeConfigRequiredError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except YouTubeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ActiveStreamError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("", summary="Get active stream")
async def get_stream_status(camera_manager: CameraManagerDependency) -> StreamView:
    """Get current stream status."""
    if (stream_info := await camera_manager.get_stream_info()) is None:
        raise HTTPException(404, "No stream active")
    return stream_info


@router.delete(
    "",
    status_code=204,
    summary="Stop YouTube streaming",
    responses={204: {"description": "Active YouTube stream stopped."}, 404: {"description": "No active stream."}},
)
async def stop_stream(camera_manager: CameraManagerDependency) -> None:
    """Stop active YouTube stream."""
    if not camera_manager.stream.is_active:
        raise HTTPException(404, "No stream active")
    try:
        return await camera_manager.stop_streaming()
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
