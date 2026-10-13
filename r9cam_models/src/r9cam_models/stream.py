"""Provider-neutral models for stream transport contracts."""

from enum import StrEnum

from pydantic import AnyUrl, AwareDatetime, BaseModel, Field, PositiveFloat, computed_field

from .images import BaseMetadata


class StreamMode(StrEnum):
    """Active stream provider/mode."""

    YOUTUBE = "youtube"


class StreamMetadata(BaseMetadata):
    """Metadata specific to video streams."""

    @computed_field(return_type=PositiveFloat | None)
    @property
    def fps(self) -> PositiveFloat | None:
        """Frames per second calculated from frame duration."""
        if self.capture_metadata.frame_duration:
            return round(1_000_000 / self.capture_metadata.frame_duration, 3)
        return None


class StreamView(BaseModel):
    """Provider-neutral public model for active stream information."""

    mode: StreamMode
    provider: str = Field(description="Stream provider identifier")
    url: AnyUrl
    started_at: AwareDatetime
    metadata: StreamMetadata
