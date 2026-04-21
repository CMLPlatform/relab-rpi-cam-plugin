"""Tests for the non-Raspberry-Pi hardware stubs.

These stubs exist so the app can boot on macOS / Linux dev machines where
``picamera2`` is unavailable. Every instance-creating stub should fail loudly
so misuse is obvious, while the ``create_*_configuration`` helpers return empty
dicts so callers can still exercise configuration code paths.
"""

from __future__ import annotations

import pytest

from app.camera.services.hardware_stubs import (
    FfmpegOutputStub,
    H264EncoderStub,
    Picamera2Stub,
)


class TestPicamera2StubRaises:
    """Instantiating the stub should raise so callers don't silently no-op."""

    def test_constructor_raises_runtime_error(self) -> None:
        """The bare constructor raises so misuse fails loudly in dev."""
        with pytest.raises(RuntimeError, match="picamera2 is not available"):
            Picamera2Stub()

    def test_constructor_raises_regardless_of_camera_num(self) -> None:
        """Any camera number still raises."""
        with pytest.raises(RuntimeError):
            Picamera2Stub(1)


class TestPicamera2StubConfigHelpers:
    """Config factories are plain dict returns so production code can call them."""

    def test_create_still_configuration_returns_empty_dict(self) -> None:
        """``create_still_configuration`` returns ``{}`` for dev hosts."""
        # Access via __new__ to bypass the guard in __init__.
        stub = Picamera2Stub.__new__(Picamera2Stub)
        assert stub.create_still_configuration() == {}

    def test_create_video_configuration_returns_empty_dict(self) -> None:
        """``create_video_configuration`` returns ``{}`` for dev hosts."""
        stub = Picamera2Stub.__new__(Picamera2Stub)
        assert stub.create_video_configuration() == {}

    def test_create_preview_configuration_returns_empty_dict(self) -> None:
        """``create_preview_configuration`` returns ``{}`` for dev hosts."""
        stub = Picamera2Stub.__new__(Picamera2Stub)
        assert stub.create_preview_configuration() == {}


class TestH264EncoderStub:
    """Constructing the encoder stub should raise so misuse fails loudly."""

    def test_constructor_raises_runtime_error(self) -> None:
        """The bare constructor raises so misuse fails loudly in dev."""
        with pytest.raises(RuntimeError, match="picamera2 is not available"):
            H264EncoderStub()


class TestFfmpegOutputStub:
    """Constructing the ffmpeg output stub should raise so misuse fails loudly."""

    def test_constructor_raises_runtime_error(self) -> None:
        """The bare constructor raises so misuse fails loudly in dev."""
        with pytest.raises(RuntimeError, match="picamera2 is not available"):
            FfmpegOutputStub("rtsp://example")
