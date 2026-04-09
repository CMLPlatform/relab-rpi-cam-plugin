"""API-edge stream request schemas and provider-specific errors."""

from pydantic import BaseModel, Field, SecretStr


class YoutubeConfigRequiredError(Exception):
    """Raised when trying to start a YouTube stream without the required config."""

    def __init__(self) -> None:
        super().__init__("Broadcast and stream key required for YouTube streaming.")


class YoutubeStreamConfig(BaseModel):
    """YouTube stream configuration passed to the stream start endpoint."""

    stream_key: SecretStr = Field(description="Stream key for YouTube streaming")
    broadcast_key: SecretStr = Field(description="Broadcast key for YouTube streaming")
