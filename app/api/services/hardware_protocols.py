"""Type-only protocols for optional hardware-backed dependencies."""

from typing import Any, Protocol


class Picamera2Like(Protocol):
    """Behavior required from a Picamera2-compatible camera object."""

    camera_properties: dict[str, Any]

    def configure(self, config: object) -> None:
        """Configure the camera for the next capture or recording."""

    def start(self) -> None:
        """Start the camera."""

    def stop(self) -> None:
        """Stop the camera."""

    def close(self) -> None:
        """Release the camera."""

    def capture_image(self) -> Any:  # noqa: ANN401
        """Capture a still image."""

    def capture_metadata(self) -> dict | None:
        """Capture metadata for the last image or frame."""

    def start_recording(self, encoder: object, output: object) -> None:
        """Start recording using the provided encoder and output."""

    def stop_recording(self) -> None:
        """Stop recording."""

    def create_still_configuration(self, **kwargs: object) -> dict:
        """Create a still capture configuration."""

    def create_video_configuration(self, **kwargs: object) -> dict:
        """Create a video capture configuration."""


class H264EncoderLike(Protocol):
    """Behavior required from an H264 encoder object."""


class FfmpegOutputLike(Protocol):
    """Behavior required from an FFmpeg output object."""
