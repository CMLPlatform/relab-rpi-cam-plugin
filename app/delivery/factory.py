"""Factory for the process-wide :class:`ImageSink` singleton.

Selection order:

1. If ``IMAGE_SINK`` is explicitly set, honour it and hard-error on bad config.
2. Otherwise infer from what's configured: ``s3_endpoint_url`` → S3,
   ``pairing_backend_url`` → backend, nothing → startup error.

There is **no** silent fallback between sinks. Misconfiguration should fail
loudly at startup, not quietly lose captures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.settings import (
    IMAGE_SINK_AUTO,
    IMAGE_SINK_BACKEND,
    IMAGE_SINK_S3,
    validate_endpoint_transport,
)
from app.delivery.backend_sink import BackendPushSink
from app.delivery.base import ImageSink

if TYPE_CHECKING:
    from app.core.settings import Settings
    from app.delivery.s3_sink import S3CompatibleSink


class ImageSinkConfigError(RuntimeError):
    """Raised at startup when image sink configuration is incomplete or inconsistent."""


def get_image_sink(settings: Settings) -> ImageSink:
    """Return the ``ImageSink`` implementation for the current settings."""
    choice = settings.image_sink
    if choice == IMAGE_SINK_BACKEND:
        return BackendPushSink()
    if choice == IMAGE_SINK_S3:
        return _build_s3_sink(settings)
    if choice == IMAGE_SINK_AUTO:
        # Prefer explicit S3 config — if both are set the user almost certainly wants S3.
        if settings.s3_endpoint_url:
            return _build_s3_sink(settings)
        if settings.pairing_backend_url:
            return BackendPushSink()
        msg = (
            "No image sink configured. Set IMAGE_SINK=backend with PAIRING_BACKEND_URL "
            "for paired-to-Relab mode, or IMAGE_SINK=s3 with the S3_* variables for "
            "standalone mode. See docker-compose.standalone.yml for a MinIO-backed example."
        )
        raise ImageSinkConfigError(msg)
    msg = f"Unknown image_sink={choice!r}. Set IMAGE_SINK=backend, s3, or auto."
    raise ImageSinkConfigError(msg)


def _build_s3_sink(settings: Settings) -> S3CompatibleSink:
    from app.delivery.s3_sink import (  # noqa: PLC0415 # Imported lazily to avoid requiring boto3 in backend-only deployments.
        S3CompatibleSink,
    )

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
    try:
        validate_endpoint_transport(
            settings.s3_endpoint_url,
            setting_name="S3_ENDPOINT_URL",
            app_env=settings.app_env,
        )
    except ValueError as exc:
        raise ImageSinkConfigError(str(exc)) from exc
    return S3CompatibleSink(
        endpoint_url=settings.s3_endpoint_url,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
        region=settings.s3_region,
        public_url_template=settings.s3_public_url_template,
    )
