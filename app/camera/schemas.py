"""API schemas for the camera feature: controls, focus, and stream start requests."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator

JsonValue = bool | int | float | str | list[Any] | dict[str, Any] | None


class FocusMode(StrEnum):
    """Friendly focus modes exposed at the API edge."""

    CONTINUOUS = "continuous"
    AUTO = "auto"
    MANUAL = "manual"


class CameraControlInfo(BaseModel):
    """Discoverable information about one backend camera control."""

    name: str
    namespace: str = "backend"
    value_type: str | None = None
    minimum: JsonValue = None
    maximum: JsonValue = None
    default: JsonValue = None
    options: list[JsonValue] | None = None
    read_only: bool = False


class CameraControlsView(BaseModel):
    """Remote control capabilities and latest observed camera values."""

    supported: bool
    controls: dict[str, CameraControlInfo] = Field(default_factory=dict)
    values: dict[str, JsonValue] = Field(default_factory=dict)


class CameraControlsPatch(BaseModel):
    """Generic camera controls patch using backend-native control names."""

    controls: dict[str, JsonValue] = Field(
        description="Backend-native control names and values.",
        examples=[{"ExposureTime": 10000, "AnalogueGain": 1.5}],
        min_length=1,
    )


class FocusControlRequest(BaseModel):
    """Request body for common focus operations."""

    mode: FocusMode = Field(description="Focus mode to apply.")
    lens_position: float | None = Field(
        default=None,
        ge=0,
        description="Manual focus lens position; only used with mode=manual.",
    )
    trigger_cycle: bool = Field(
        default=False,
        description="When mode=auto, run a one-shot autofocus cycle.",
    )


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
