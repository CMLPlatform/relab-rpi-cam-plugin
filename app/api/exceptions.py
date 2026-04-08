"""API exception classes."""

from relab_rpi_cam_models.stream import Stream


class YouTubeValidationError(Exception):
    """Raised when YouTube stream key validation fails."""

    def __init__(self, stream_key: str | None = None) -> None:
        super().__init__(f"Invalid YouTube stream key{f': {stream_key}' if stream_key else ''}.")


class ActiveStreamError(Exception):
    """Raised when trying to access the camera while a stream is active."""

    def __init__(self, stream: Stream) -> None:
        self.mode = stream.mode
        self.url = stream.url
        super().__init__(f"Stream active in {self.mode} mode at {self.url}. Stop streaming first.")


class CameraInitializationError(Exception):
    """Raised when camera initialization fails."""

    def __init__(self, camera_num: int, reason: str = "") -> None:
        msg = f"Failed to initialize camera device {camera_num}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
