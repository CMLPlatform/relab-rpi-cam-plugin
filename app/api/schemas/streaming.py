"""API-edge stream request schemas and provider-specific errors."""

import re

from pydantic import BaseModel, Field, SecretStr, field_validator


class YoutubeConfigRequiredError(Exception):
    """Raised when trying to start a YouTube stream without the required config."""

    def __init__(self) -> None:
        super().__init__("Broadcast and stream key required for YouTube streaming.")


class YoutubeStreamConfig(BaseModel):
    """YouTube stream configuration passed to the stream start endpoint."""

    stream_key: SecretStr = Field(description="Stream key for YouTube streaming")
    broadcast_key: SecretStr = Field(description="Broadcast key for YouTube streaming")

    @field_validator("stream_key", mode="before")
    @classmethod
    def _validate_stream_key(cls, v: object) -> SecretStr:
        """Ensure stream keys contain only URL-safe characters."""
        raw = v.get_secret_value() if isinstance(v, SecretStr) else str(v)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
            msg = "Invalid stream key: only URL-safe characters allowed"
            raise ValueError(msg)
        return SecretStr(raw)

    @field_validator("broadcast_key", mode="before")
    @classmethod
    def _validate_broadcast_key(cls, v: object) -> SecretStr:
        """Validate broadcast (watch) id shape conservatively.

        YouTube watch IDs are typically URL-safe; enforce the same
        conservative subset here to avoid passing unexpected characters
        into downstream URL builders.
        """
        raw = v.get_secret_value() if isinstance(v, SecretStr) else str(v)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
            msg = "Invalid broadcast key: only URL-safe characters allowed"
            raise ValueError(msg)
        return SecretStr(raw)
