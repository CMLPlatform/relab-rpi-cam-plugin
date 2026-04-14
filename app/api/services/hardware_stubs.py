"""Runtime fallbacks for picamera2 when running on non-Raspberry Pi hosts."""

from typing import Any, ClassVar

_ERR = "picamera2 is not available; camera operations require a Raspberry Pi."


class Picamera2Stub:
    """Stub used on non-Raspberry Pi hosts so the app can start."""

    camera_properties: ClassVar[dict[str, Any]] = {}

    def __init__(self, _camera_num: int = 0) -> None:
        raise RuntimeError(_ERR)

    def configure(self, config: object) -> None:
        """Configure the stub camera."""

    def start(self) -> None:
        """Start the stub camera."""

    def stop(self) -> None:
        """Stop the stub camera."""

    def close(self) -> None:
        """Close the stub camera."""

    def capture_image(self, _name: str = "main") -> object:
        """Capture a stub image (returns a PIL Image when running on Pi)."""

    def capture_metadata(self) -> dict | None:
        """Return stub capture metadata."""

    def switch_mode_and_capture_image(self, _config: object) -> object:
        """Switch to a stub config and capture a stub image."""

    def start_recording(self, encoder: object, output: object) -> None:
        """Start stub recording."""

    def stop_recording(self) -> None:
        """Stop stub recording."""

    def start_encoder(self, encoder: object, output: object, *, name: str = "main") -> None:
        """Stub attach an encoder to a named stream."""

    def stop_encoder(self, *, name: str = "main") -> None:
        """Stub detach an encoder from a named stream."""

    def create_still_configuration(self, **_kwargs: object) -> dict:
        """Create a stub still configuration."""
        return {}

    def create_video_configuration(self, **_kwargs: object) -> dict:
        """Create a stub video configuration."""
        return {}

    def create_preview_configuration(self, **_kwargs: object) -> dict:
        """Create a stub preview configuration."""
        return {}


class H264EncoderStub:
    """Stub used on non-Raspberry Pi hosts so the app can start."""

    def __init__(self) -> None:
        raise RuntimeError(_ERR)


class FfmpegOutputStub:
    """Stub for non-Raspberry Pi environments."""

    def __init__(self, _output_str: str, /, **_kwargs: object) -> None:
        raise RuntimeError(_ERR)
