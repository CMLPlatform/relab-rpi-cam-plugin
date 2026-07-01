"""API schemas for the camera feature: controls, focus, and stream start requests."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Self, cast

from pydantic import BaseModel, Field, RootModel, SecretStr, field_validator, model_validator

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

JsonValue = bool | int | float | str | list[Any] | dict[str, Any] | None

MAX_CAMERA_CONTROL_COUNT = 32
MAX_CAMERA_CONTROL_NAME_LENGTH = 64
MAX_CAMERA_CONTROL_STRING_LENGTH = 256
MAX_CAMERA_CONTROL_COLLECTION_ITEMS = 16
MAX_CAMERA_CONTROL_DEPTH = 4
MAX_CAMERA_CONTROL_BYTES = 4 * 1024
CAMERA_CONTROL_NAME_PATTERN = r"[A-Za-z][A-Za-z0-9_]*"
MAX_UPLOAD_METADATA_KEYS = 16
MAX_UPLOAD_METADATA_KEY_LENGTH = 64
MAX_UPLOAD_METADATA_STRING_LENGTH = 1024
MAX_UPLOAD_METADATA_COLLECTION_ITEMS = 16
MAX_UPLOAD_METADATA_DEPTH = 4
MAX_UPLOAD_METADATA_BYTES = 4 * 1024
CameraControlsMap = Annotated[dict[str, JsonValue], Field(min_length=1, max_length=MAX_CAMERA_CONTROL_COUNT)]
UploadMetadataMap = Annotated[dict[str, JsonValue], Field(max_length=MAX_UPLOAD_METADATA_KEYS)]


@dataclass(frozen=True)
class _JsonLimits:
    field_name: str
    max_keys: int
    max_key_length: int
    max_string_length: int
    max_items: int
    max_depth: int
    max_serialized_bytes: int
    key_pattern: str | None = None


_CONTROL_LIMITS = _JsonLimits(
    field_name="controls",
    max_keys=MAX_CAMERA_CONTROL_COUNT,
    max_key_length=MAX_CAMERA_CONTROL_NAME_LENGTH,
    max_string_length=MAX_CAMERA_CONTROL_STRING_LENGTH,
    max_items=MAX_CAMERA_CONTROL_COLLECTION_ITEMS,
    max_depth=MAX_CAMERA_CONTROL_DEPTH,
    max_serialized_bytes=MAX_CAMERA_CONTROL_BYTES,
    key_pattern=CAMERA_CONTROL_NAME_PATTERN,
)
_UPLOAD_METADATA_LIMITS = _JsonLimits(
    field_name="upload_metadata",
    max_keys=MAX_UPLOAD_METADATA_KEYS,
    max_key_length=MAX_UPLOAD_METADATA_KEY_LENGTH,
    max_string_length=MAX_UPLOAD_METADATA_STRING_LENGTH,
    max_items=MAX_UPLOAD_METADATA_COLLECTION_ITEMS,
    max_depth=MAX_UPLOAD_METADATA_DEPTH,
    max_serialized_bytes=MAX_UPLOAD_METADATA_BYTES,
)


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

    controls: CameraControlsMap = Field(
        description="Backend-native control names and values.",
        examples=[{"ExposureTime": 10000, "AnalogueGain": 1.5}],
    )

    @field_validator("controls")
    @classmethod
    def _validate_controls(cls, v: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _validate_json_mapping(v, _CONTROL_LIMITS)


class CaptureUploadMetadata(RootModel[dict[str, JsonValue]]):
    """Bounded opaque metadata forwarded to the configured image sink."""

    root: UploadMetadataMap

    @field_validator("root")
    @classmethod
    def _validate_upload_metadata(cls, v: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _validate_json_mapping(v, _UPLOAD_METADATA_LIMITS)


def _validate_json_mapping(value: Mapping[str, Any], limits: _JsonLimits) -> dict[str, JsonValue]:
    result = dict(value)
    key_re = re.compile(limits.key_pattern) if limits.key_pattern else None
    _validate_json_value(
        result,
        limits=limits,
        key_re=key_re,
        depth=0,
    )

    try:
        size = len(json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except ValueError as exc:
        msg = f"{limits.field_name} must contain only finite JSON numbers"
        raise ValueError(msg) from exc
    if size > limits.max_serialized_bytes:
        msg = f"{limits.field_name} must serialize to at most {limits.max_serialized_bytes} bytes"
        raise ValueError(msg)
    return result


def _validate_json_value(
    value: object,
    *,
    limits: _JsonLimits,
    key_re: re.Pattern[str] | None,
    depth: int,
) -> None:
    if depth > limits.max_depth:
        msg = f"{limits.field_name} may be nested at most {limits.max_depth} levels"
        raise ValueError(msg)
    if _is_json_scalar(value, limits):
        return
    if isinstance(value, list):
        _validate_json_sequence(
            value,
            limits=limits,
            key_re=key_re,
            depth=depth,
        )
        return
    if isinstance(value, dict):
        _validate_json_object(
            cast("Mapping[object, object]", value),
            limits=limits,
            key_re=key_re,
            depth=depth,
        )
        return

    msg = f"{limits.field_name} values must be JSON scalars, arrays, or objects"
    raise ValueError(msg)


def _is_json_scalar(value: object, limits: _JsonLimits) -> bool:
    if value is None or isinstance(value, bool | int):
        return True
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = f"{limits.field_name} must contain only finite JSON numbers"
            raise ValueError(msg)
        return True
    if isinstance(value, str):
        if len(value) > limits.max_string_length:
            msg = f"{limits.field_name} strings may be at most {limits.max_string_length} characters"
            raise ValueError(msg)
        return True
    return False


def _validate_json_sequence(
    value: Sequence[object],
    *,
    limits: _JsonLimits,
    key_re: re.Pattern[str] | None,
    depth: int,
) -> None:
    if len(value) > limits.max_items:
        msg = f"{limits.field_name} arrays may include at most {limits.max_items} items"
        raise ValueError(msg)
    for item in value:
        _validate_json_value(
            item,
            limits=limits,
            key_re=key_re,
            depth=depth + 1,
        )


def _validate_json_object(
    value: Mapping[object, object],
    *,
    limits: _JsonLimits,
    key_re: re.Pattern[str] | None,
    depth: int,
) -> None:
    if len(value) > limits.max_keys:
        msg = f"{limits.field_name} objects may include at most {limits.max_keys} keys"
        raise ValueError(msg)
    for key, item in value.items():
        key_text = str(key)
        if len(key_text) > limits.max_key_length:
            msg = f"{limits.field_name} keys may be at most {limits.max_key_length} characters"
            raise ValueError(msg)
        if key_re and not key_re.fullmatch(key_text):
            msg = f"{limits.field_name} keys contain unsupported characters"
            raise ValueError(msg)
        _validate_json_value(
            item,
            limits=limits,
            key_re=key_re,
            depth=depth + 1,
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

    @model_validator(mode="after")
    def _validate_mode_fields(self) -> Self:
        if self.lens_position is not None and self.mode != FocusMode.MANUAL:
            msg = "lens_position is only valid when mode is manual"
            raise ValueError(msg)
        if self.trigger_cycle and self.mode != FocusMode.AUTO:
            msg = "trigger_cycle is only valid when mode is auto"
            raise ValueError(msg)
        return self


def _validate_youtube_key(v: object, field: str) -> SecretStr:
    raw = v.get_secret_value() if isinstance(v, SecretStr) else str(v)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        msg = f"Invalid {field}: only URL-safe characters allowed"
        raise ValueError(msg)
    return SecretStr(raw)


class YoutubeStreamConfig(BaseModel):
    """YouTube stream configuration passed to the stream start endpoint."""

    stream_key: SecretStr = Field(description="Stream key for YouTube streaming")
    broadcast_key: SecretStr = Field(description="Broadcast key for YouTube streaming")

    @field_validator("stream_key", mode="before")
    @classmethod
    def _validate_stream_key(cls, v: object) -> SecretStr:
        return _validate_youtube_key(v, "stream key")

    @field_validator("broadcast_key", mode="before")
    @classmethod
    def _validate_broadcast_key(cls, v: object) -> SecretStr:
        return _validate_youtube_key(v, "broadcast key")
