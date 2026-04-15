"""Image sink that pushes captures to the paired Relab backend."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.api.services.image_sinks.base import ImageSinkError, StoredImage
from app.utils.backend_client import BackendUploadError, upload_image

if TYPE_CHECKING:
    from collections.abc import Mapping


class BackendPushSink:
    """``ImageSink`` backed by the existing Pi→backend HTTPS upload path.

    This is a thin wrapper around :func:`app.utils.backend_client.upload_image`
    that translates ``BackendUploadError`` into ``ImageSinkError`` so the
    common caller (``CameraManager.capture_jpeg``) can handle all sinks
    uniformly.

    Pairing + network failures are the normal ``ImageSinkError`` case —
    callers are expected to enqueue for retry rather than surface them to the
    user. See :mod:`app.utils.upload_queue`.
    """

    async def put(
        self,
        *,
        image_id: str,  # noqa: ARG002 — signature consistency with ImageSink Protocol
        image_bytes: bytes,
        filename: str,
        capture_metadata: Mapping[str, object],
        upload_metadata: Mapping[str, object],
    ) -> StoredImage:
        """Push the capture to the Relab backend and return its stored URL."""
        try:
            uploaded = await upload_image(
                image_bytes=image_bytes,
                filename=filename,
                capture_metadata=capture_metadata,
                upload_metadata=upload_metadata,
            )
        except BackendUploadError as exc:
            raise ImageSinkError(str(exc)) from exc

        return StoredImage(
            image_id=uploaded.image_id,
            image_url=uploaded.image_url,
        )
