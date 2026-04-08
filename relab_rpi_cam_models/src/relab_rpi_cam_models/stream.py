"""Models for Stream information."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import AnyUrl, AwareDatetime, BaseModel, Field, PositiveFloat, SecretStr

from .images import BaseMetadata, CameraProperties, CaptureMetadata


### Custom Exceptions ###
class YoutubeConfigRequiredError(Exception):
    """Raised when trying to start a YouTube stream without providing a YouTube config."""

    def __init__(self) -> None:
        super().__init__("Broadcast and stream key required for YouTube streaming.")


class StreamStateError(Exception):
    """Raised when stream state is inconsistent."""

    def __init__(self, msg: str | None = None) -> None:
        super().__init__(msg or "Stream state is inconsistent.")


### Pydantic Models ###
class YoutubeStreamConfig(BaseModel):
    """YouTube stream configuration."""

    stream_key: SecretStr = Field(description="Stream key for YouTube streaming")
    broadcast_key: SecretStr = Field(description="Broadcast key for YouTube streaming")


class StreamMode(StrEnum):
    """Stream mode."""

    YOUTUBE = "youtube"


class StreamMetadata(BaseMetadata):
    """Metadata specific to video streams."""

    @property
    def fps(self) -> PositiveFloat | None:
        """Frames per second calculated from frame duration."""
        if self.capture_metadata.frame_duration:
            return round(1_000_000 / self.capture_metadata.frame_duration, 3)
        return None

    @classmethod
    def from_metadata(cls, camera_properties: dict, capture_metadata: dict) -> "StreamMetadata":
        """Create a StreamMetadata instance from raw camera capture data."""
        return cls(
            camera_properties=CameraProperties.model_validate(camera_properties),
            capture_metadata=CaptureMetadata.model_validate(capture_metadata),
        )


class StreamView(BaseModel):
    """Pydantic model for active stream information."""

    mode: StreamMode
    url: AnyUrl
    started_at: AwareDatetime
    youtube_config: YoutubeStreamConfig | None = None
    metadata: StreamMetadata


@dataclass
class Stream:
    """Main Stream model."""

    mode: StreamMode | None = None
    url: AnyUrl | None = None
    started_at: AwareDatetime | None = None
    youtube_config: YoutubeStreamConfig | None = None

    @property
    def is_active(self) -> bool:
        """Return True if a stream is currently active."""
        return self.mode is not None

    def get_info(self, camera_properties: dict, capture_metadata: dict) -> StreamView | None:
        """Get stream information including metadata if active.

        Depends on camera properties and capture metadata.
        """
        if not self.is_active:
            return None

        metadata = StreamMetadata.from_metadata(camera_properties, capture_metadata)

        # Validate stream state consistency
        if self.mode is None:
            raise StreamStateError(msg="Stream mode is None but stream is marked as active")
        if self.url is None:
            raise StreamStateError(msg="Stream URL is None but stream is marked as active")
        if self.started_at is None:
            raise StreamStateError(msg="Stream start time is None but stream is marked as active")

        return StreamView(
            mode=self.mode,
            url=self.url,
            metadata=metadata,
            started_at=self.started_at,
            youtube_config=self.youtube_config,
        )
