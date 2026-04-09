"""API exception classes."""

from app.api.services.stream_state import ActiveStreamState


class YouTubeValidationError(Exception):
    """Raised when YouTube stream key validation fails."""

    def __init__(self) -> None:
        super().__init__("Invalid YouTube stream key.")


class ActiveStreamError(Exception):
    """Raised when trying to access the camera while a stream is active."""

    def __init__(self, stream: ActiveStreamState) -> None:
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
