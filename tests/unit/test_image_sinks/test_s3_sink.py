"""Tests for S3CompatibleSink.

``aioboto3`` is not a runtime dependency of the plugin — the sink imports it
lazily inside ``put()``, and the standalone compose profile adds it via a
MinIO-friendly install layer. Tests stub the import via ``sys.modules`` so the
Pi suite never has to pull in ~30MB of boto3 transitively.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.services.image_sinks.base import ImageSinkError
from app.api.services.image_sinks.s3_sink import S3CompatibleSink


class _FakeS3Client:
    """Minimal async-context-manager stand-in for ``aioboto3.Session().client("s3")``."""

    def __init__(self) -> None:
        self.put_object = AsyncMock(return_value={"ETag": '"deadbeef"'})

    async def __aenter__(self) -> _FakeS3Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def _install_fake_aioboto3(*, client: _FakeS3Client | None = None) -> MagicMock:
    """Install a fake ``aioboto3`` module so the sink's lazy import finds it."""
    client = client or _FakeS3Client()
    session_instance = SimpleNamespace(client=lambda *_args, **_kwargs: client)

    fake_session_cls = MagicMock(return_value=session_instance)
    fake_module = SimpleNamespace(Session=fake_session_cls)
    sys.modules["aioboto3"] = fake_module
    return fake_session_cls


@pytest.fixture(autouse=True)
def _cleanup_fake_aioboto3() -> None:
    """Remove any fake ``aioboto3`` module after each test."""
    yield
    sys.modules.pop("aioboto3", None)


def _make_sink(**overrides: str) -> S3CompatibleSink:
    return S3CompatibleSink(
        endpoint_url=overrides.get("endpoint_url", "http://minio.local:9000"),
        bucket=overrides.get("bucket", "rpi-cam"),
        access_key_id=overrides.get("access_key_id", "ak"),
        secret_access_key=overrides.get("secret_access_key", "sk"),
        region=overrides.get("region", "us-east-1"),
        public_url_template=overrides.get("public_url_template", "{endpoint}/{bucket}/{key}"),
    )


class TestS3CompatibleSink:
    """Happy-path + error translation + key layout + URL template."""

    async def test_put_uploads_and_returns_public_url(self) -> None:
        """The sink should PUT into the bucket and return the templated public URL."""
        client = _FakeS3Client()
        _install_fake_aioboto3(client=client)

        sink = _make_sink()
        result = await sink.put(
            image_id="abc123",
            image_bytes=b"jpeg-body",
            filename="abc123.jpg",
            capture_metadata={},
            upload_metadata={"product_id": 42},
        )

        client.put_object.assert_awaited_once()
        kwargs = client.put_object.await_args.kwargs
        assert kwargs["Bucket"] == "rpi-cam"
        assert kwargs["Key"] == "rpi-cam/42/abc123.jpg"
        assert kwargs["Body"] == b"jpeg-body"
        assert kwargs["ContentType"] == "image/jpeg"

        assert result.image_id == "abc123"
        assert str(result.image_url) == "http://minio.local:9000/rpi-cam/rpi-cam/42/abc123.jpg"

    async def test_missing_product_id_routes_to_unsorted(self) -> None:
        """If the upload_metadata has no product_id, the key goes under ``unsorted/``."""
        client = _FakeS3Client()
        _install_fake_aioboto3(client=client)

        sink = _make_sink()
        await sink.put(
            image_id="img-no-product",
            image_bytes=b"jpeg",
            filename="img.jpg",
            capture_metadata={},
            upload_metadata={},  # no product_id
        )

        kwargs = client.put_object.await_args.kwargs
        assert kwargs["Key"] == "rpi-cam/unsorted/img-no-product.jpg"

    async def test_custom_public_url_template_for_cdn_fronted_bucket(self) -> None:
        """A custom template (e.g. for R2 custom domains) should be honoured."""
        client = _FakeS3Client()
        _install_fake_aioboto3(client=client)

        sink = _make_sink(public_url_template="https://cdn.example.com/{key}")
        result = await sink.put(
            image_id="xyz",
            image_bytes=b"jpeg",
            filename="xyz.jpg",
            capture_metadata={},
            upload_metadata={"product_id": 9},
        )

        assert str(result.image_url) == "https://cdn.example.com/rpi-cam/9/xyz.jpg"

    async def test_put_object_failure_translates_to_image_sink_error(self) -> None:
        """Any exception from aioboto3 should be wrapped as ``ImageSinkError``."""
        client = _FakeS3Client()
        client.put_object = AsyncMock(side_effect=RuntimeError("access denied"))
        _install_fake_aioboto3(client=client)

        sink = _make_sink()
        with pytest.raises(ImageSinkError, match="S3 upload failed"):
            await sink.put(
                image_id="doomed",
                image_bytes=b"jpeg",
                filename="doomed.jpg",
                capture_metadata={},
                upload_metadata={"product_id": 1},
            )

    async def test_missing_aioboto3_import_raises_image_sink_error(self) -> None:
        """If ``aioboto3`` isn't installed, the sink surfaces a helpful error."""
        # No fake module installed — the lazy import should actually fail.
        sys.modules.pop("aioboto3", None)
        # Also block it from finding the real module if it happens to be installed.
        sys.modules["aioboto3"] = None  # type: ignore[assignment]

        sink = _make_sink()
        try:
            with pytest.raises(ImageSinkError, match="aioboto3 is required"):
                await sink.put(
                    image_id="nope",
                    image_bytes=b"jpeg",
                    filename="nope.jpg",
                    capture_metadata={},
                    upload_metadata={},
                )
        finally:
            sys.modules.pop("aioboto3", None)
