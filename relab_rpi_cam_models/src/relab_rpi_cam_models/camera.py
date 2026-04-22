"""Models for Camera information and management logic."""

from enum import StrEnum

from pydantic import BaseModel

from .stream import StreamView


class CameraMode(StrEnum):
    """Camera use mode. Contains camera configuration for each mode."""

    PHOTO = "photo"
    VIDEO = "video"


class CameraStatusView(BaseModel):
    """API response model for camera status."""

    current_mode: CameraMode | None = None
    stream: StreamView | None = None
