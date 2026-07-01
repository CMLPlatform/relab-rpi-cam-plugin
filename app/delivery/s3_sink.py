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

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import AnyUrl

from app.delivery.base import ImageSinkError, StoredImage
from app.observability.logging import build_log_extra

if TYPE_CHECKING:
    from collections.abc import Mapping

aioboto3: Any = None
BotocoreConfig: Any = None
try:
    import aioboto3 as _aioboto3
    from botocore.config import Config as _BotocoreConfig
except ImportError:
    pass
else:
    aioboto3 = _aioboto3
    BotocoreConfig = _BotocoreConfig

logger = logging.getLogger(__name__)

_DEFAULT_KEY_PREFIX = "rpi-cam"
_DEFAULT_REGION = "us-east-1"
_BUCKET_ALREADY_CODES = {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}
_FALLBACK_PRODUCT_SEGMENT = "unsorted"
_MAX_OBJECT_KEY_SEGMENT_LENGTH = 64
_UNSAFE_SEGMENT_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")
_REPEATED_DASHES = re.compile(r"-+")


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
        if aioboto3 is None or BotocoreConfig is None:
            msg = (
                "aioboto3 is required for S3CompatibleSink. "
                "Install the [s3] extra with `uv sync --group s3` "
                "or switch to IMAGE_SINK=backend."
            )
            raise RuntimeError(msg)
        self._endpoint_url = endpoint_url.rstrip("/")
        self._bucket = bucket
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._region = region
        self._public_url_template = public_url_template
        self._key_prefix = key_prefix.strip("/")
        self._bucket_ensured = False
        self._session = aioboto3.Session()
        # Shared client kept warm for the process lifetime so consecutive
        # uploads (notably an UploadQueue backlog drain) reuse the same
        # TCP/TLS connection pool instead of paying a fresh handshake each put.
        self._client: Any | None = None
        self._client_stack: contextlib.AsyncExitStack | None = None
        self._client_lock = asyncio.Lock()

    async def put(
        self,
        *,
        image_id: str,
        image_bytes: bytes,
        filename: str,
        capture_metadata: Mapping[str, object],
        upload_metadata: Mapping[str, object],
    ) -> StoredImage:
        """Upload to S3 and return the public URL."""
        del filename, capture_metadata

        key = self._build_object_key(image_id, upload_metadata)

        try:
            s3 = await self._get_client()
            await self._ensure_bucket(s3)
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=image_bytes,
                ContentType="image/jpeg",
            )
            public_url = AnyUrl(self._build_public_url(key))
        except Exception as exc:
            msg = f"S3 upload failed for key {key!r}: {exc}"
            raise ImageSinkError(msg) from exc

        logger.info("Uploaded capture %s to S3 bucket %s (%s)", image_id, self._bucket, key, extra=build_log_extra())
        return StoredImage(image_id=image_id, image_url=public_url)

    async def _get_client(self) -> Any:  # noqa: ANN401  # aioboto3 client is dynamically typed
        """Return the shared S3 client, creating it once and keeping the pool warm.

        ``aioboto3`` clients are async context managers bound to one event
        loop. The sink is a process-wide singleton on the app's single loop,
        so we enter the client once (guarded against the concurrent
        capture/drain callers) and reuse it for every upload.
        """
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                stack = contextlib.AsyncExitStack()
                self._client = await stack.enter_async_context(
                    self._session.client(
                        "s3",
                        endpoint_url=self._endpoint_url,
                        aws_access_key_id=self._access_key_id,
                        aws_secret_access_key=self._secret_access_key,
                        region_name=self._region,
                        config=BotocoreConfig(
                            connect_timeout=5,
                            read_timeout=30,
                            retries={"max_attempts": 2, "mode": "standard"},
                            max_pool_connections=4,
                        ),
                    )
                )
                self._client_stack = stack
        return self._client

    async def aclose(self) -> None:
        """Close the shared S3 client and its connection pool. Idempotent."""
        async with self._client_lock:
            if self._client_stack is not None:
                await self._client_stack.aclose()
                self._client_stack = None
                self._client = None

    async def _ensure_bucket(self, s3: Any) -> None:  # noqa: ANN401  # aioboto3 client is dynamically typed
        """Create the bucket if it doesn't already exist (idempotent)."""
        if self._bucket_ensured:
            return
        try:
            kwargs: dict[str, object] = {"Bucket": self._bucket}
            if self._region and self._region != _DEFAULT_REGION:
                kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
            await s3.create_bucket(**kwargs)
            logger.info("Created S3 bucket %r at %s", self._bucket, self._endpoint_url, extra=build_log_extra())
        except Exception as exc:
            # Swallow "bucket already exists" errors from any S3-compatible server.
            # Both codes appear across AWS S3 / MinIO / RustFS depending on ownership.
            response = getattr(exc, "response", None)
            code = (response or {}).get("Error", {}).get("Code", "")
            if code not in _BUCKET_ALREADY_CODES:
                raise
        self._bucket_ensured = True

    def _build_object_key(
        self,
        image_id: str,
        upload_metadata: Mapping[str, object],
    ) -> str:
        """Build the S3 object key for this capture."""
        product_id = upload_metadata.get("product_id")
        product_segment = _safe_object_key_segment(product_id)
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


def _safe_object_key_segment(value: object) -> str:
    """Return one bounded S3 object-key segment from untrusted metadata."""
    if value is None:
        return _FALLBACK_PRODUCT_SEGMENT
    parts = [part for part in str(value).replace("\\", "/").split("/") if part not in {"", ".", ".."}]
    joined = "-".join(parts)
    segment = _UNSAFE_SEGMENT_CHARS.sub("-", joined)
    segment = _REPEATED_DASHES.sub("-", segment).strip("-.")[:_MAX_OBJECT_KEY_SEGMENT_LENGTH].strip("-.")
    return segment or _FALLBACK_PRODUCT_SEGMENT
