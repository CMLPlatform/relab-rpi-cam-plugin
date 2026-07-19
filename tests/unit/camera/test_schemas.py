"""Tests for camera request schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.camera.schemas import (
    MAX_CAMERA_CONTROL_COLLECTION_ITEMS,
    MAX_CAMERA_CONTROL_DEPTH,
    MAX_CAMERA_CONTROL_NAME_LENGTH,
    MAX_CAMERA_CONTROL_STRING_LENGTH,
    MAX_UPLOAD_METADATA_KEY_LENGTH,
    MAX_UPLOAD_METADATA_KEYS,
    CameraControlsPatch,
    CaptureUploadMetadata,
    FocusControlRequest,
    FocusMode,
)

MANUAL_LENS_POSITION = 2.5


class TestFocusControlRequest:
    """Focus request business-rule validation."""

    def test_manual_focus_accepts_optional_lens_position(self) -> None:
        """Manual mode may set a lens position."""
        request = FocusControlRequest(mode=FocusMode.MANUAL, lens_position=MANUAL_LENS_POSITION)

        assert request.mode == FocusMode.MANUAL
        assert request.lens_position == MANUAL_LENS_POSITION

    def test_manual_focus_accepts_no_lens_position(self) -> None:
        """Manual mode can switch mode without forcing a lens move."""
        request = FocusControlRequest(mode=FocusMode.MANUAL)

        assert request.mode == FocusMode.MANUAL
        assert request.lens_position is None

    def test_auto_focus_accepts_trigger_cycle(self) -> None:
        """Auto mode may request a one-shot autofocus cycle."""
        request = FocusControlRequest(mode=FocusMode.AUTO, trigger_cycle=True)

        assert request.mode == FocusMode.AUTO
        assert request.trigger_cycle is True

    @pytest.mark.parametrize(
        "payload",
        [
            {"mode": "continuous", "lens_position": 1.0},
            {"mode": "auto", "lens_position": 1.0},
            {"mode": "continuous", "trigger_cycle": True},
            {"mode": "manual", "trigger_cycle": True},
        ],
    )
    def test_rejects_mode_irrelevant_fields(self, payload: dict[str, object]) -> None:
        """Focus fields must match the selected business operation."""
        with pytest.raises(ValidationError):
            FocusControlRequest.model_validate(payload)


def _nested(depth: int) -> object:
    """Return a value nested ``depth`` levels below the top-level mapping value."""
    value: object = 1
    for _ in range(depth):
        value = {"a": value}
    return value


class TestCameraControlsPatch:
    """Bounds enforced on the opaque, backend-native controls mapping."""

    def test_accepts_nested_json_within_limits(self) -> None:
        """Scalars, arrays, and shallow objects are all legal control values."""
        patch = CameraControlsPatch.model_validate(
            {"controls": {"ExposureTime": 10000, "Window": [1, 2, 3], "Meta": {"on": True, "note": None}}}
        )

        assert patch.controls["ExposureTime"] == 10000

    @pytest.mark.parametrize(
        "controls",
        [
            pytest.param({"Exposure Time": 1}, id="key-off-pattern"),
            pytest.param({"1Exposure": 1}, id="key-leading-digit"),
            pytest.param({"E" * (MAX_CAMERA_CONTROL_NAME_LENGTH + 1): 1}, id="key-too-long"),
            pytest.param({"Note": "x" * (MAX_CAMERA_CONTROL_STRING_LENGTH + 1)}, id="string-too-long"),
            pytest.param({"Window": list(range(MAX_CAMERA_CONTROL_COLLECTION_ITEMS + 1))}, id="array-too-long"),
            pytest.param({"Deep": _nested(MAX_CAMERA_CONTROL_DEPTH + 1)}, id="nested-too-deep"),
            pytest.param({"Gain": float("inf")}, id="infinity"),
            pytest.param({"Gain": float("nan")}, id="nan"),
            pytest.param(
                {f"K{i}": "x" * MAX_CAMERA_CONTROL_STRING_LENGTH for i in range(16)},
                id="serialized-too-large",
            ),
        ],
    )
    def test_rejects_out_of_bounds_controls(self, controls: dict[str, object]) -> None:
        """Untrusted control payloads must not slip past the bounds check."""
        with pytest.raises(ValidationError):
            CameraControlsPatch.model_validate({"controls": controls})

    def test_rejects_empty_controls(self) -> None:
        """An empty patch is a client mistake, not a no-op."""
        with pytest.raises(ValidationError):
            CameraControlsPatch.model_validate({"controls": {}})


class TestCaptureUploadMetadata:
    """Bounds enforced on opaque metadata forwarded to the image sink."""

    def test_accepts_empty_metadata(self) -> None:
        """Metadata is optional, so an empty mapping is valid."""
        assert CaptureUploadMetadata.model_validate({}).root == {}

    def test_allows_keys_that_controls_would_reject(self) -> None:
        """Sink metadata has no control-name pattern, so free-form keys are fine."""
        metadata = CaptureUploadMetadata.model_validate({"experiment id": "run-1"})

        assert metadata.root["experiment id"] == "run-1"

    @pytest.mark.parametrize(
        "metadata",
        [
            {f"k{i}": i for i in range(MAX_UPLOAD_METADATA_KEYS + 1)},
            {"k" * (MAX_UPLOAD_METADATA_KEY_LENGTH + 1): 1},
            {"deep": _nested(MAX_CAMERA_CONTROL_DEPTH + 1)},
            {"bad": float("nan")},
        ],
    )
    def test_rejects_out_of_bounds_metadata(self, metadata: dict[str, object]) -> None:
        """Oversized metadata must be refused before it reaches the sink."""
        with pytest.raises(ValidationError):
            CaptureUploadMetadata.model_validate(metadata)
