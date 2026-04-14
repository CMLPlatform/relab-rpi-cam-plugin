"""Tests for image capture and preview endpoints."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode

from app.api.services.camera_manager import CameraManager
from app.api.services.image_sinks.base import ImageSinkError, StoredImage
from app.core.config import settings
from app.utils.upload_queue import UploadQueue
from tests.constants import (
    JPEG_CONTENT_TYPE,
    QUEUED_STATUS,
    SAMPLE_IMAGE_ID,
    SAMPLE_IMAGE_URL,
    UPLOADED_STATUS,
)

CAPTURED_STATUS_KEY = "status"
IMAGE_ID_KEY = "image_id"
IMAGE_URL_KEY = "image_url"
CONFLICT_RESPONSE_CODE = "409"


class _StubSink:
    """In-memory image sink driven by a pytest fixture."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.put = AsyncMock(side_effect=self._put)

    async def _put(self, **_kwargs: object) -> StoredImage:
        if self._fail:
            raise ImageSinkError("network unreachable")
        return StoredImage(
            image_id=SAMPLE_IMAGE_ID,
            image_url=AnyUrl(SAMPLE_IMAGE_URL),
        )


@pytest.fixture
def stub_success_sink(camera_manager: CameraManager) -> _StubSink:
    """Swap the camera manager's image sink for a happy-path stub."""
    sink = _StubSink(fail=False)
    camera_manager._sink = sink  # noqa: SLF001 — test hook
    camera_manager._upload_queue = None  # noqa: SLF001 — force the queue to re-resolve with the stub sink
    return sink


@pytest.fixture
def stub_failing_sink(camera_manager: CameraManager) -> _StubSink:
    """Swap the camera manager's image sink for a failing stub."""
    sink = _StubSink(fail=True)
    camera_manager._sink = sink  # noqa: SLF001
    camera_manager._upload_queue = None  # noqa: SLF001
    return sink


class TestPreviewEndpoint:
    """Tests for GET /images/preview."""

    async def test_preview_returns_jpeg(self, client: AsyncClient) -> None:
        """Test that the preview endpoint returns a JPEG image."""
        resp = await client.get("/images/preview")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == JPEG_CONTENT_TYPE

    async def test_preview_does_not_write_files(self, client: AsyncClient, tmp_path: Path) -> None:
        """Preview frames should not be persisted to disk."""
        original = settings.image_path
        settings.image_path = tmp_path
        try:
            resp = await client.get("/images/preview")
            assert resp.status_code == 200
            assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []
        finally:
            settings.image_path = original

    async def test_preview_runtime_error_returns_500(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that if capturing the preview JPEG raises a RuntimeError, a 500 response is returned."""

        async def _boom() -> bytes:
            msg = "boom"
            raise RuntimeError(msg)

        monkeypatch.setattr(camera_manager, "capture_preview_jpeg", _boom)
        resp = await client.get("/images/preview")
        assert resp.status_code == 500

    async def test_preview_while_streaming_returns_jpeg(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Preview still reads from the persistent main stream while YouTube is active."""
        camera_manager.stream.mode = StreamMode.YOUTUBE
        resp = await client.get("/images/preview")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == JPEG_CONTENT_TYPE

    async def test_openapi_does_not_document_stream_conflict(self, client: AsyncClient) -> None:
        """Preview capture is expected to work while YouTube streaming is active."""
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        preview_get = resp.json()["paths"]["/images/preview"]["get"]
        assert CONFLICT_RESPONSE_CODE not in preview_get["responses"]


class TestCaptureEndpoint:
    """Tests for POST /images — synchronous sink put + queue fallback."""

    async def test_capture_pushes_and_returns_uploaded_status(
        self,
        client: AsyncClient,
        stub_success_sink: _StubSink,
        tmp_path: Path,
    ) -> None:
        """Happy path: synchronous sink put returns status=uploaded with the sink's URL."""
        original = settings.image_path
        settings.image_path = tmp_path

        try:
            resp = await client.post("/images", json={"product_id": 1})
            assert resp.status_code == 201
            data = resp.json()
            assert data[CAPTURED_STATUS_KEY] == UPLOADED_STATUS
            assert data[IMAGE_ID_KEY] == SAMPLE_IMAGE_ID
            assert data[IMAGE_URL_KEY] == SAMPLE_IMAGE_URL
        finally:
            settings.image_path = original

        assert stub_success_sink.put.await_count == 1

    async def test_capture_deletes_local_file_after_upload(
        self,
        client: AsyncClient,
        stub_success_sink: _StubSink,  # noqa: ARG002 — fixture seeds the sink
        tmp_path: Path,
    ) -> None:
        """After a successful push the local JPEG should be gone — single source of truth."""
        original = settings.image_path
        settings.image_path = tmp_path

        try:
            resp = await client.post("/images", json=None)
            assert resp.status_code == 201

            remaining = await asyncio.to_thread(lambda: list(tmp_path.glob("*.jpg")))
            assert remaining == []
        finally:
            settings.image_path = original

    async def test_capture_queues_on_sink_failure(
        self,
        client: AsyncClient,
        stub_failing_sink: _StubSink,
        tmp_path: Path,
    ) -> None:
        """A failing sink should enqueue the capture and return status=queued."""
        original = settings.image_path
        settings.image_path = tmp_path

        try:
            resp = await client.post("/images", json={"product_id": 7})
            assert resp.status_code == 201
            data = resp.json()
            assert data[CAPTURED_STATUS_KEY] == QUEUED_STATUS
            assert data[IMAGE_URL_KEY] is None
        finally:
            settings.image_path = original

        assert stub_failing_sink.put.await_count == 1
        # Queued file lives under data/queue/ with a .json sidecar.
        queue_root = tmp_path / "queue"
        jpgs = list(queue_root.glob("*.jpg"))
        jsons = list(queue_root.glob("*.json"))
        assert len(jpgs) == 1
        assert len(jsons) == 1

    async def test_capture_forwards_upload_metadata_to_sink(
        self,
        client: AsyncClient,
        stub_success_sink: _StubSink,
        tmp_path: Path,
    ) -> None:
        """The upload_metadata body should arrive verbatim at ``sink.put``."""
        original = settings.image_path
        settings.image_path = tmp_path

        try:
            resp = await client.post(
                "/images",
                json={"product_id": 99, "description": "rear view"},
            )
            assert resp.status_code == 201
        finally:
            settings.image_path = original

        call = stub_success_sink.put.await_args
        assert call is not None
        assert call.kwargs["upload_metadata"] == {"product_id": 99, "description": "rear view"}

    async def test_capture_runtime_error_returns_500(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RuntimeError during capture should surface as 500."""
        monkeypatch.setattr(camera_manager, "capture_jpeg", AsyncMock(side_effect=RuntimeError("boom")))
        resp = await client.post("/images")
        assert resp.status_code == 500
