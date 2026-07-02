"""Media capture file policies used by local storage and upload flows."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError


class CaptureFileValidationError(RuntimeError):
    """Raised when capture bytes do not match the configured media policy."""


@dataclass(frozen=True, slots=True)
class CaptureFilePolicy:
    """Validation policy for one generated capture media type."""

    extension: str
    media_type: str
    pillow_format: str

    def filename_for(self, capture_id: str) -> str:
        """Return the trusted filename for an internally generated capture id."""
        return f"{capture_id}{self.extension}"

    def validate_dimensions(self, size: tuple[int, int], *, max_pixels: int) -> None:
        """Validate image dimensions against the configured pixel budget."""
        width, height = size
        pixels = width * height
        if pixels > max_pixels:
            msg = f"Capture has {pixels} pixels, which exceeds the {max_pixels} pixel limit."
            raise CaptureFileValidationError(msg)

    def validate_size(self, data: bytes, *, max_bytes: int) -> None:
        """Validate encoded capture bytes against the configured byte budget."""
        if len(data) > max_bytes:
            msg = f"Capture file is {len(data)} bytes, which exceeds the {max_bytes} byte limit."
            raise CaptureFileValidationError(msg)

    def validate_persisted_bytes(self, data: bytes, *, max_bytes: int, max_pixels: int) -> None:
        """Validate persisted capture bytes before retry or serving."""
        self.validate_size(data, max_bytes=max_bytes)
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format != self.pillow_format:
                    msg = f"Capture file is not a valid {self.pillow_format} image."
                    raise CaptureFileValidationError(msg)
                image.verify()
            with Image.open(BytesIO(data)) as image:
                self.validate_dimensions(image.size, max_pixels=max_pixels)
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            msg = f"Capture file is not a valid {self.pillow_format} image."
            raise CaptureFileValidationError(msg) from exc


JPEG_CAPTURE_POLICY = CaptureFilePolicy(extension=".jpg", media_type="image/jpeg", pillow_format="JPEG")
