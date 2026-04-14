"""Backend abstraction for hardware-specific camera implementations.

The core ``CameraBackend`` Protocol covers what every camera must do: open,
capture a still, and clean up. Backends for non-standard sensors (infrared,
hyperspectral, etc.) only have to satisfy this core. Backends that also do live
video streaming additionally implement ``StreamingCameraBackend``; the camera
manager uses ``isinstance`` checks to gate streaming operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import AnyUrl

from app.api.schemas.camera_controls import (
    CameraControlsCapabilities,
    CameraControlsView,
    FocusControlRequest,
    JsonValue,
)
from app.api.schemas.streaming import YoutubeStreamConfig
from app.api.services.hardware_protocols import Picamera2Like

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


@runtime_checkable
class CameraBackend(Protocol):
    """Core behaviour required from every camera backend."""

    current_mode: CameraMode | None

    @property
    def camera(self) -> Picamera2Like | None:
        """The live hardware camera handle, or ``None`` if not yet opened."""

    async def open(self, mode: CameraMode) -> None:
        """Prepare the backend for the requested camera mode."""

    async def capture_image(self) -> CaptureResult:
        """Capture a full-resolution image and metadata."""

    async def cleanup(self) -> None:
        """Release backend resources."""


@runtime_checkable
class StreamingCameraBackend(CameraBackend, Protocol):
    """Extension for backends capable of live video streaming (YouTube, etc.)."""

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


@runtime_checkable
class ControllableCameraBackend(CameraBackend, Protocol):
    """Extension for backends that expose runtime camera controls."""

    async def get_controls(self) -> CameraControlsView:
        """Return supported controls and latest observed values."""

    async def get_controls_capabilities(self) -> CameraControlsCapabilities:
        """Return a UI-friendly list of supported controls."""

    async def set_controls(self, controls: dict[str, JsonValue]) -> CameraControlsView:
        """Apply backend-native camera controls and return the updated view."""

    async def set_focus(self, request: FocusControlRequest) -> CameraControlsView:
        """Apply a friendly focus request and return the updated view."""
