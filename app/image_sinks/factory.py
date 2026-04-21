"""Factory for the process-wide :class:`ImageSink` singleton.

Selection order:

1. If ``IMAGE_SINK`` is explicitly set, honour it and hard-error on bad config.
2. Otherwise infer from what's configured: ``pairing_backend_url`` → backend,
   ``s3_endpoint_url`` → S3, nothing → startup error.

There is **no** silent fallback between sinks. Misconfiguration should fail
loudly at startup, not quietly lose captures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.image_sinks.backend_sink import BackendPushSink
from app.image_sinks.base import ImageSink

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.image_sinks.s3_sink import S3CompatibleSink


class ImageSinkConfigError(RuntimeError):
    """Raised at startup when image sink configuration is incomplete or inconsistent."""


_IMAGE_SINK_BACKEND = "backend"
_IMAGE_SINK_S3 = "s3"
_IMAGE_SINK_AUTO = "auto"


def get_image_sink(settings: Settings) -> ImageSink:
    """Return the ``ImageSink`` implementation for the current settings."""
    choice = settings.image_sink
    if choice == _IMAGE_SINK_BACKEND:
        return _build_backend_sink(settings)
    if choice == _IMAGE_SINK_S3:
        return _build_s3_sink(settings)
    if choice == _IMAGE_SINK_AUTO:
        return _infer_sink(settings)
    msg = (
        f"Unknown image_sink={choice!r}. Set IMAGE_SINK={_IMAGE_SINK_BACKEND}, {_IMAGE_SINK_S3}, or {_IMAGE_SINK_AUTO}."
    )
    raise ImageSinkConfigError(msg)


def _build_backend_sink(_settings: Settings) -> BackendPushSink:
    """Build a ``BackendPushSink``.

    We deliberately don't require ``pairing_backend_url`` to be set here —
    the Pi is allowed to come up unpaired and capture into the upload queue
    for retry after pairing completes. ``backend_client.upload_image`` fails
    loudly at call time if the device isn't paired.
    """
    return BackendPushSink()


def _build_s3_sink(settings: Settings) -> S3CompatibleSink:
    """Build an ``S3CompatibleSink`` or fail loudly on missing credentials."""
    from app.image_sinks.s3_sink import S3CompatibleSink  # noqa: PLC0415

    missing = [
        name
        for name, value in (
            ("S3_ENDPOINT_URL", settings.s3_endpoint_url),
            ("S3_BUCKET", settings.s3_bucket),
            ("S3_ACCESS_KEY_ID", settings.s3_access_key_id),
            ("S3_SECRET_ACCESS_KEY", settings.s3_secret_access_key),
        )
        if not value
    ]
    if missing:
        msg = f"IMAGE_SINK=s3 requires {', '.join(missing)} — refusing to start without them."
        raise ImageSinkConfigError(msg)

    return S3CompatibleSink(
        endpoint_url=settings.s3_endpoint_url,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
        region=settings.s3_region,
        public_url_template=settings.s3_public_url_template,
    )


def _infer_sink(settings: Settings) -> ImageSink:
    """When ``image_sink`` is not explicitly set, pick based on what's configured."""
    # Prefer explicit S3 config — if both are set the user has almost
    # certainly configured S3 for a reason and wants that path.
    if settings.s3_endpoint_url:
        return _build_s3_sink(settings)
    if settings.pairing_backend_url:
        return _build_backend_sink(settings)
    msg = (
        "No image sink configured. Set IMAGE_SINK=backend with PAIRING_BACKEND_URL "
        "for paired-to-Relab mode, or IMAGE_SINK=s3 with the S3_* variables for "
        "standalone mode. See docker-compose.standalone.yml for a MinIO-backed example."
    )
    raise ImageSinkConfigError(msg)
