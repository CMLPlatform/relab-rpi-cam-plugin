"""Stub classes for picamera2 when running on non-Raspberry Pi hosts."""

from typing import Any, ClassVar

_ERR = "picamera2 is not available; camera operations require a Raspberry Pi."


class Picamera2Stub:
    """Stub used on non-Raspberry Pi hosts so the app can start."""

    camera_properties: ClassVar[dict[str, Any]] = {}

    def __init__(self, camera_num: int = 0) -> None:  # noqa: ARG002
        raise RuntimeError(_ERR)

    def configure(self, config: object) -> None:
        ...

    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def close(self) -> None:
        ...

    def capture_image(self) -> Any:  # noqa: ANN401
        ...

    def capture_metadata(self) -> dict | None:
        ...

    def start_recording(self, encoder: object, output: object) -> None:
        ...

    def stop_recording(self) -> None:
        ...

    def create_still_configuration(self, **kwargs: object) -> dict:  # noqa: ARG002
        return {}

    def create_video_configuration(self, **kwargs: object) -> dict:  # noqa: ARG002
        return {}


class H264EncoderStub:
    """Stub used on non-Raspberry Pi hosts so the app can start."""

    def __init__(self) -> None:
        raise RuntimeError(_ERR)


class FfmpegOutputStub:
    """Stub for non-Raspberry Pi environments."""

    def __init__(self, output_str: str, /, **kwargs: object) -> None:
        pass
