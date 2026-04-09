"""Backend abstraction for hardware-specific camera implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import AnyUrl

from app.api.schemas.streaming import YoutubeStreamConfig

if TYPE_CHECKING:
    from PIL.Image import Image as PilImage
    from relab_rpi_cam_models.camera import CameraMode
    from relab_rpi_cam_models.stream import StreamMode
else:
    PilImage = object


@dataclass(frozen=True)
class CaptureResult:
    """Hardware capture result used by the camera manager."""

    image: PilImage
    camera_properties: dict[str, Any]
    capture_metadata: dict[str, Any]


@dataclass(frozen=True)
class StreamStartResult:
    """Result returned when a backend starts a stream."""

    mode: StreamMode
    url: AnyUrl


class CameraBackend(Protocol):
    """Behavior required from a concrete camera backend."""

    current_mode: CameraMode | None

    async def open(self, mode: CameraMode) -> None:
        """Prepare the backend for the requested camera mode."""

    async def capture_image(self) -> CaptureResult:
        """Capture a full-resolution image and metadata."""

    async def capture_preview_jpeg(self) -> bytes:
        """Capture a preview JPEG suitable for the UI."""

    async def start_stream(
        self,
        mode: StreamMode,
        *,
        youtube_config: YoutubeStreamConfig | None = None,
    ) -> StreamStartResult:
        """Start a stream for the requested provider/mode."""

    async def stop_stream(self) -> None:
        """Stop the currently active stream."""

    async def get_stream_metadata(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return camera properties and current stream capture metadata."""

    async def cleanup(self) -> None:
        """Release backend resources."""
