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
