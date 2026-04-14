"""Contract for pluggable image sinks.

Post-Phase-10 the Pi has two ways to park a captured JPEG:

1. ``BackendPushSink`` — push to the paired Relab backend via HTTPS + device
   JWT. This is the default for a camera paired to a user's Relab account.
2. ``S3CompatibleSink`` — PUT directly into a user-owned S3-compatible bucket
   (MinIO, Backblaze B2, Cloudflare R2, Wasabi, plain AWS S3). This makes the
   "standalone Pi camera" use case work without any Relab backend.

Both satisfy the same :class:`ImageSink` Protocol so callers (the
``CameraManager.capture_jpeg`` happy path and the ``UploadQueue`` retry loop)
don't care which one is configured. Swapping sinks is an env-var change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import AnyUrl

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class StoredImage:
    """Result of a successful ``ImageSink.put`` call."""

    image_id: str
    image_url: AnyUrl
    expires_at: datetime | None = None


class ImageSinkError(RuntimeError):
    """Raised when an ``ImageSink`` cannot deliver a capture.

    Callers should treat this as "retry later via the upload queue" rather
    than "the capture is lost forever". The upload queue's exponential
    backoff + dead-letter behaviour covers terminal failures.
    """


@runtime_checkable
class ImageSink(Protocol):
    """Where captured image bytes end up.

    Implementations must be safe to call concurrently from
    ``CameraManager.capture_jpeg`` and from ``UploadQueue.drain_once``: the
    same instance is shared between them.
    """

    async def put(
        self,
        *,
        image_id: str,
        image_bytes: bytes,
        filename: str,
        capture_metadata: Mapping[str, object],
        upload_metadata: Mapping[str, object],
    ) -> StoredImage:
        """Store the captured bytes and return a handle to the stored object.

        Raises :class:`ImageSinkError` on any failure — the caller decides
        whether to queue for retry or surface the error to the user.
        """
