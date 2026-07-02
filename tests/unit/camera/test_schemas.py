"""Tests for camera request schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.camera.schemas import FocusControlRequest, FocusMode

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
