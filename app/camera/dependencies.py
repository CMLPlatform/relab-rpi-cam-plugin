"""Camera management dependencies for FastAPI."""

import contextlib
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request

from app.camera.services.manager import CameraManager
from app.core.config import settings
from app.core.runtime import get_request_runtime
from app.observability.logging import build_log_extra

logger = logging.getLogger(__name__)


def get_camera_manager(request: Request) -> CameraManager:
    """Fetch the camera manager from the request runtime."""
    return get_request_runtime(request).camera_manager


CameraManagerDependency = Annotated[CameraManager, Depends(get_camera_manager)]


def _stream_mode(manager: CameraManager) -> object | None:
    """Return the current stream mode without assuming every test double has it."""
    return getattr(manager.stream, "mode", None)


async def check_stream_duration(manager: CameraManager | None = None) -> None:
    """Stop streams that exceed maximum duration."""
    if manager is None:
        msg = "check_stream_duration requires an explicit CameraManager"
        raise RuntimeError(msg)
    active_manager = manager
    if (
        active_manager.stream.is_active
        and active_manager.stream.started_at
        and (datetime.now(UTC) - active_manager.stream.started_at).total_seconds() > settings.max_stream_duration_s
    ):
        try:
            await active_manager.stop_streaming()
        except RuntimeError as e:
            logger.exception(
                "Failed to stop stream when exceeding max duration",
                exc_info=e,
                extra=build_log_extra(stream_mode=_stream_mode(active_manager)),
            )


async def check_stream_health(manager: CameraManager | None = None) -> None:
    """Monitor stream health: verify the stream is still active and recording.

    If the stream becomes unhealthy (e.g., ffmpeg crashed), stops the stream
    to allow recovery on next start request.
    """
    if manager is None:
        msg = "check_stream_health requires an explicit CameraManager"
        raise RuntimeError(msg)
    active_manager = manager
    if not active_manager.stream.is_active:
        return

    try:
        # Try to get stream info — this will fail if camera is not recording properly
        stream_info = await active_manager.get_stream_info()
        if stream_info is None:
            logger.warning(
                "Stream info became unavailable; stopping stream",
                extra=build_log_extra(stream_mode=_stream_mode(active_manager)),
            )
            await active_manager.stop_streaming()
    except (OSError, RuntimeError) as e:
        logger.warning(
            "Stream health check failed: %s. Stopping stream for recovery.",
            e,
            extra=build_log_extra(stream_mode=_stream_mode(active_manager)),
        )
        with contextlib.suppress(RuntimeError):
            await active_manager.stop_streaming()
