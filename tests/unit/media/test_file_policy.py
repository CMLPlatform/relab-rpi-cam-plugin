"""Tests for generated JPEG capture validation."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.media.file_policy import JPEG_CAPTURE_POLICY, CaptureFilePolicy, CaptureFileValidationError

JPEG_EXTENSION = ".jpg"
JPEG_MEDIA_TYPE = "image/jpeg"


def _jpeg_bytes(*, size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color="blue").save(buffer, format="JPEG")
    return buffer.getvalue()


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color="blue").save(buffer, format="PNG")
    return buffer.getvalue()


def test_validation_error_is_runtime_error() -> None:
    """Validation failures should flow through capture runtime-error handling."""
    assert issubclass(CaptureFileValidationError, RuntimeError)


def test_jpeg_capture_policy_shape() -> None:
    """The active policy should describe today's generated JPEG captures."""
    assert isinstance(JPEG_CAPTURE_POLICY, CaptureFilePolicy)
    assert JPEG_CAPTURE_POLICY.extension == JPEG_EXTENSION
    assert JPEG_CAPTURE_POLICY.media_type == JPEG_MEDIA_TYPE
    assert JPEG_CAPTURE_POLICY.filename_for("a" * 32) == f"{'a' * 32}.jpg"


def test_valid_jpeg_bytes_pass() -> None:
    """Valid JPEG bytes under configured limits should validate cleanly."""
    JPEG_CAPTURE_POLICY.validate_persisted_bytes(_jpeg_bytes(), max_bytes=1024, max_pixels=100)


def test_non_jpeg_bytes_fail() -> None:
    """Capture validation should not accept arbitrary bytes."""
    with pytest.raises(CaptureFileValidationError, match="valid JPEG"):
        JPEG_CAPTURE_POLICY.validate_persisted_bytes(b"not a jpeg", max_bytes=1024, max_pixels=100)


def test_non_jpeg_image_bytes_fail() -> None:
    """Capture validation should reject valid non-JPEG image formats."""
    with pytest.raises(CaptureFileValidationError, match="valid JPEG"):
        JPEG_CAPTURE_POLICY.validate_persisted_bytes(_png_bytes(), max_bytes=1024, max_pixels=100)


def test_oversized_byte_payload_fails() -> None:
    """Byte limits should reject captures before upload or queue retry."""
    with pytest.raises(CaptureFileValidationError, match="exceeds"):
        JPEG_CAPTURE_POLICY.validate_persisted_bytes(_jpeg_bytes(), max_bytes=4, max_pixels=100)


def test_oversized_pixel_dimensions_fail() -> None:
    """Pixel limits should reject image floods before upload or serving."""
    with pytest.raises(CaptureFileValidationError, match="pixels"):
        JPEG_CAPTURE_POLICY.validate_persisted_bytes(_jpeg_bytes(size=(8, 8)), max_bytes=2048, max_pixels=10)
