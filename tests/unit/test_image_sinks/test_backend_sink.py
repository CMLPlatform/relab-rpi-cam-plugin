"""Tests for BackendPushSink — the Phase 6A backend push path behind the Phase 10 sink interface."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import AnyUrl

from app.api.services.image_sinks import backend_sink as backend_sink_mod
from app.api.services.image_sinks.backend_sink import BackendPushSink
from app.api.services.image_sinks.base import ImageSinkError, StoredImage
from app.utils.backend_client import BackendUploadError, UploadedImageInfo


class TestBackendPushSink:
    """Happy-path + error translation."""

    async def test_put_forwards_to_upload_image_and_returns_stored_image(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The sink should proxy every field to ``upload_image`` and return its result."""
        mock_upload = AsyncMock(
            return_value=UploadedImageInfo(
                image_id="srv-abc",
                image_url=AnyUrl("https://backend.example/images/abc.jpg"),
            )
        )
        monkeypatch.setattr(backend_sink_mod, "upload_image", mock_upload)

        sink = BackendPushSink()
        result = await sink.put(
            image_id="local-1",
            image_bytes=b"jpeg-body",
            filename="local-1.jpg",
            capture_metadata={"iso": 200},
            upload_metadata={"product_id": 7},
        )

        assert isinstance(result, StoredImage)
        assert result.image_id == "srv-abc"
        assert str(result.image_url) == "https://backend.example/images/abc.jpg"

        mock_upload.assert_awaited_once()
        kwargs = mock_upload.await_args.kwargs
        assert kwargs["image_bytes"] == b"jpeg-body"
        assert kwargs["filename"] == "local-1.jpg"
        assert kwargs["capture_metadata"] == {"iso": 200}
        assert kwargs["upload_metadata"] == {"product_id": 7}

    async def test_backend_upload_error_translates_to_image_sink_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``BackendUploadError`` from the HTTPS client must bubble up as ``ImageSinkError``."""
        monkeypatch.setattr(
            backend_sink_mod,
            "upload_image",
            AsyncMock(side_effect=BackendUploadError("network unreachable")),
        )

        sink = BackendPushSink()
        with pytest.raises(ImageSinkError, match="network unreachable"):
            await sink.put(
                image_id="local-2",
                image_bytes=b"bytes",
                filename="local-2.jpg",
                capture_metadata={},
                upload_metadata={},
            )
