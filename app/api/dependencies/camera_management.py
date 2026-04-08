"""Camera management dependencies for FastAPI."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends

from app.api.services.camera_manager import CameraManager
from app.core.config import settings

camera_manager = CameraManager()

logger = logging.getLogger(__name__)


def get_camera_manager() -> CameraManager:
    """Fetch the camera manager singleton."""
    return camera_manager


CameraManagerDependency = Annotated[CameraManager, Depends(get_camera_manager)]


async def camera_to_standby() -> None:
    """Close camera instance if there is no active stream."""
    if not camera_manager.stream.is_active:
        await camera_manager.cleanup()


async def check_stream_duration() -> None:
    """Stop streams that exceed maximum duration."""
    if (
        camera_manager.stream.is_active
        and camera_manager.stream.started_at
        and (datetime.now(UTC) - camera_manager.stream.started_at).total_seconds() > settings.max_stream_duration_s
    ):
        try:
            await camera_manager.stop_streaming()
        except RuntimeError as e:
            logger.exception("Failed to stop stream when exceeding max duration", exc_info=e)


async def check_stream_health() -> None:
    """Monitor stream health: verify the stream is still active and recording.

    If the stream becomes unhealthy (e.g., ffmpeg crashed), stops the stream
    to allow recovery on next start request.
    """
    if not camera_manager.stream.is_active:
        return

    try:
        # Try to get stream info — this will fail if camera is not recording properly
        stream_info = await camera_manager.get_stream_info()
        if stream_info is None:
            logger.warning("Stream info became unavailable; stopping stream")
            await camera_manager.stop_streaming()
    except Exception as e:  # noqa: BLE001
        logger.warning("Stream health check failed: %s. Stopping stream for recovery.", e)
        try:
            await camera_manager.stop_streaming()
        except RuntimeError:
            pass  # Already stopped or error — stream is unhealthy either way
