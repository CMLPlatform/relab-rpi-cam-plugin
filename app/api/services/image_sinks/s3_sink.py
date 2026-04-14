"""Image sink that PUTs captures directly to an S3-compatible bucket.

Targets any server that speaks S3: MinIO (the default for the standalone
docker-compose profile), Backblaze B2, Cloudflare R2, Wasabi, and plain AWS
S3. The configuration surface is intentionally the same shape as any S3 SDK
credential bundle, so users can paste values from their existing IAM creds.

The object key layout is ``<prefix>/<product-id-or-unsorted>/<image_id>.jpg``
where ``<prefix>`` defaults to ``rpi-cam``. The public URL resolution uses a
template so users can front the bucket with a CDN (R2 custom domains,
CloudFront, etc.) and have the Pi return the fronted URL to the frontend.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import AnyUrl

from app.api.services.image_sinks.base import ImageSinkError, StoredImage

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

_DEFAULT_KEY_PREFIX = "rpi-cam"


class S3CompatibleSink:
    """``ImageSink`` that PUTs JPEGs straight into an S3-compatible bucket."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        public_url_template: str,
        key_prefix: str = _DEFAULT_KEY_PREFIX,
    ) -> None:
        self._endpoint_url = endpoint_url.rstrip("/")
        self._bucket = bucket
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._region = region
        self._public_url_template = public_url_template
        self._key_prefix = key_prefix.strip("/")

    async def put(
        self,
        *,
        image_id: str,
        image_bytes: bytes,
        filename: str,  # noqa: ARG002 — kept for the ImageSink Protocol signature
        capture_metadata: Mapping[str, object],  # noqa: ARG002
        upload_metadata: Mapping[str, object],
    ) -> StoredImage:
        """Upload to S3 and return the public URL."""
        # Lazy import: ``aioboto3`` is an optional dependency (see the ``[s3]``
        # extra in pyproject.toml) — we don't want to pay its ~30MB install
        # cost on deployments that use ``BackendPushSink``. Importing it here
        # also keeps unit tests simple: the other sinks' tests don't need
        # aioboto3 on their ``sys.path``.
        try:
            import aioboto3  # noqa: PLC0415
        except ImportError as exc:
            msg = (
                "aioboto3 is required for S3CompatibleSink. "
                "Install the extra with `uv sync --extra s3` "
                "(or `pip install 'relab-rpi-cam-plugin[s3]'`), "
                "or switch to IMAGE_SINK=backend."
            )
            raise ImageSinkError(msg) from exc

        key = self._build_object_key(image_id, upload_metadata)

        try:
            session = aioboto3.Session()
            async with session.client(
                "s3",
                endpoint_url=self._endpoint_url,
                aws_access_key_id=self._access_key_id,
                aws_secret_access_key=self._secret_access_key,
                region_name=self._region,
            ) as s3:
                await s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=image_bytes,
                    ContentType="image/jpeg",
                )
        except Exception as exc:  # noqa: BLE001 — aioboto3 surfaces a zoo of errors; flatten all of them.
            msg = f"S3 upload failed for key {key!r}: {exc}"
            raise ImageSinkError(msg) from exc

        public_url = self._build_public_url(key)
        logger.info("Uploaded capture %s to S3 bucket %s (%s)", image_id, self._bucket, key)
        return StoredImage(image_id=image_id, image_url=AnyUrl(public_url))

    def _build_object_key(
        self,
        image_id: str,
        upload_metadata: Mapping[str, object],
    ) -> str:
        """Build the S3 object key for this capture."""
        product_id = upload_metadata.get("product_id")
        product_segment = str(product_id) if product_id is not None else "unsorted"
        return f"{self._key_prefix}/{product_segment}/{image_id}.jpg"

    def _build_public_url(self, key: str) -> str:
        """Resolve the public URL via the configured template.

        Template variables: ``{endpoint}``, ``{bucket}``, ``{key}``. Default
        template gives ``{endpoint}/{bucket}/{key}`` which is the MinIO and
        path-style S3 shape. For virtual-hosted S3 or CDN-fronted buckets the
        user overrides it via ``S3_PUBLIC_URL_TEMPLATE``.
        """
        return self._public_url_template.format(
            endpoint=self._endpoint_url,
            bucket=self._bucket,
            key=key,
        )
