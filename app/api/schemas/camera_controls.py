"""API schemas for remote camera controls."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

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
