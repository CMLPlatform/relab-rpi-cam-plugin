"""Tests for S3CompatibleSink."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.image_sinks.base import ImageSinkError
from app.image_sinks.s3_sink import S3CompatibleSink
from tests.constants import (
    DEFAULT_S3_REGION,
    S3_BUCKET_ALREADY_EXISTS,
    S3_BUCKET_ALREADY_OWNED_BY_YOU,
    S3_BUCKET_NAME,
    S3_CDN_IMAGE_ID,
    S3_CDN_URL,
    S3_IMAGE_BYTES,
    S3_IMAGE_ID,
    S3_MEDIA_TYPE,
    S3_OBJECT_KEY,
    S3_OBJECT_KEY_UNSORTED,
    S3_PUBLIC_URL,
    S3_UNSORTED_IMAGE_ID,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Self


class _FakeS3Client:
    """Minimal async-context-manager stand-in for ``aioboto3.Session().client("s3")``."""

    def __init__(self) -> None:
        self.put_object = AsyncMock(return_value={"ETag": '"deadbeef"'})
        self.create_bucket = AsyncMock(return_value={})

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def _make_sink(**overrides: str) -> S3CompatibleSink:
    return S3CompatibleSink(
        endpoint_url=overrides.get("endpoint_url", "http://minio.local:9000"),
        bucket=overrides.get("bucket", "rpi-cam"),
        access_key_id=overrides.get("access_key_id", "ak"),
        secret_access_key=overrides.get("secret_access_key", "sk"),
        region=overrides.get("region", DEFAULT_S3_REGION),
        public_url_template=overrides.get("public_url_template", "{endpoint}/{bucket}/{key}"),
    )


@pytest.fixture
def fake_s3_client() -> _FakeS3Client:
    """Provide a fresh fake S3 client for each test."""
    return _FakeS3Client()


@pytest.fixture(autouse=True)
def _patch_aioboto3(fake_s3_client: _FakeS3Client) -> Iterator[MagicMock]:
    """Patch ``aioboto3.Session`` so no real AWS calls are made."""
    session_instance = MagicMock()
    session_instance.client.return_value = fake_s3_client
    with patch("app.image_sinks.s3_sink.aioboto3") as mock_aioboto3:
        mock_aioboto3.Session.return_value = session_instance
        yield mock_aioboto3


class TestS3CompatibleSink:
    """Happy-path + error translation + key layout + URL template."""

    async def test_put_uploads_and_returns_public_url(self, fake_s3_client: _FakeS3Client) -> None:
        """The sink should PUT into the bucket and return the templated public URL."""
        sink = _make_sink()
        result = await sink.put(
            image_id=S3_IMAGE_ID,
            image_bytes=S3_IMAGE_BYTES,
            filename="abc123.jpg",
            capture_metadata={},
            upload_metadata={"product_id": 42},
        )

        fake_s3_client.put_object.assert_awaited_once()
        kwargs = fake_s3_client.put_object.await_args_list[0].kwargs
        assert kwargs["Bucket"] == S3_BUCKET_NAME
        assert kwargs["Key"] == S3_OBJECT_KEY
        assert kwargs["Body"] == S3_IMAGE_BYTES
        assert kwargs["ContentType"] == S3_MEDIA_TYPE

        assert result.image_id == S3_IMAGE_ID
        assert str(result.image_url) == S3_PUBLIC_URL

    async def test_missing_product_id_routes_to_unsorted(self, fake_s3_client: _FakeS3Client) -> None:
        """If the upload_metadata has no product_id, the key goes under ``unsorted/``."""
        sink = _make_sink()
        await sink.put(
            image_id=S3_UNSORTED_IMAGE_ID,
            image_bytes=b"jpeg",
            filename="img.jpg",
            capture_metadata={},
            upload_metadata={},  # no product_id
        )

        kwargs = fake_s3_client.put_object.await_args_list[0].kwargs
        assert kwargs["Key"] == S3_OBJECT_KEY_UNSORTED

    async def test_custom_public_url_template_for_cdn_fronted_bucket(self) -> None:
        """A custom template (e.g. for R2 custom domains) should be honoured."""
        sink = _make_sink(public_url_template="https://cdn.example.com/{key}")
        result = await sink.put(
            image_id=S3_CDN_IMAGE_ID,
            image_bytes=b"jpeg",
            filename="xyz.jpg",
            capture_metadata={},
            upload_metadata={"product_id": 9},
        )

        assert str(result.image_url) == S3_CDN_URL

    async def test_put_object_failure_translates_to_image_sink_error(self, fake_s3_client: _FakeS3Client) -> None:
        """Any exception from aioboto3 should be wrapped as ``ImageSinkError``."""
        fake_s3_client.put_object = AsyncMock(side_effect=RuntimeError("access denied"))

        sink = _make_sink()
        with pytest.raises(ImageSinkError, match="S3 upload failed"):
            await sink.put(
                image_id="doomed",
                image_bytes=b"jpeg",
                filename="doomed.jpg",
                capture_metadata={},
                upload_metadata={"product_id": 1},
            )

    async def test_bucket_created_on_first_put(self, fake_s3_client: _FakeS3Client) -> None:
        """``create_bucket`` should be called exactly once across multiple puts."""
        sink = _make_sink()
        await sink.put(
            image_id="first",
            image_bytes=b"jpeg",
            filename="first.jpg",
            capture_metadata={},
            upload_metadata={},
        )
        await sink.put(
            image_id="second",
            image_bytes=b"jpeg",
            filename="second.jpg",
            capture_metadata={},
            upload_metadata={},
        )

        fake_s3_client.create_bucket.assert_awaited_once()

    async def test_bucket_already_exists_is_not_an_error(self, fake_s3_client: _FakeS3Client) -> None:
        """``BucketAlreadyOwnedByYou`` / ``BucketAlreadyExists`` should be swallowed."""
        for code in (S3_BUCKET_ALREADY_OWNED_BY_YOU, S3_BUCKET_ALREADY_EXISTS):
            fake_s3_client.create_bucket = AsyncMock(
                side_effect=ClientError({"Error": {"Code": code, "Message": ""}}, "CreateBucket")
            )
            sink = _make_sink()
            # Should not raise.
            await sink.put(
                image_id="img",
                image_bytes=b"jpeg",
                filename="img.jpg",
                capture_metadata={},
                upload_metadata={},
            )
