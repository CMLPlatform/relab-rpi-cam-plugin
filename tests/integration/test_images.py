"""Tests for image capture endpoint + queue-fallback behaviour."""

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from pydantic import AnyUrl

from app.camera.services.manager import CameraManager
from app.core.settings import settings
from app.image_sinks.base import ImageSinkError, StoredImage
from tests.constants import (
    QUEUED_STATUS,
    SAMPLE_IMAGE_ID,
    SAMPLE_IMAGE_URL,
    UPLOADED_STATUS,
)

CAPTURED_STATUS_KEY = "status"
IMAGE_ID_KEY = "image_id"
IMAGE_URL_KEY = "image_url"


class _StubSink:
    """In-memory image sink driven by a pytest fixture."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.put = AsyncMock(side_effect=self._put)

    async def _put(self, **_kwargs: object) -> StoredImage:
        if self._fail:
            msg = "network unreachable"
            raise ImageSinkError(msg)
        return StoredImage(
            image_id=SAMPLE_IMAGE_ID,
            image_url=AnyUrl(SAMPLE_IMAGE_URL),
        )


@pytest.fixture
def stub_success_sink(camera_manager: CameraManager) -> _StubSink:
    """Swap the camera manager's image sink for a happy-path stub."""
    sink = _StubSink(fail=False)
    camera_manager_any = cast("Any", camera_manager)
    camera_manager_any._sink = sink
    camera_manager_any._upload_queue = None
    return sink


@pytest.fixture
def stub_failing_sink(camera_manager: CameraManager) -> _StubSink:
    """Swap the camera manager's image sink for a failing stub."""
    sink = _StubSink(fail=True)
    camera_manager_any = cast("Any", camera_manager)
    camera_manager_any._sink = sink
    camera_manager_any._upload_queue = None
    return sink


class TestCaptureEndpoint:
    """Tests for POST /captures — synchronous sink put + queue fallback."""

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
            resp = await client.post("/captures", json={"product_id": 1})
            assert resp.status_code == 201
            data = resp.json()
            assert data[CAPTURED_STATUS_KEY] == UPLOADED_STATUS
            assert data[IMAGE_ID_KEY] == SAMPLE_IMAGE_ID
            assert data[IMAGE_URL_KEY] == SAMPLE_IMAGE_URL
        finally:
            settings.image_path = original

        assert stub_success_sink.put.await_count == 1

    @pytest.mark.usefixtures("stub_success_sink")
    async def test_capture_deletes_local_file_after_upload(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """After a successful push the local JPEG should be gone — single source of truth."""
        original = settings.image_path
        settings.image_path = tmp_path

        try:
            resp = await client.post("/captures", json=None)
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
            resp = await client.post("/captures", json={"product_id": 7})
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
                "/captures",
                json={"product_id": 99, "description": "rear view"},
            )
            assert resp.status_code == 201
        finally:
            settings.image_path = original

        call = stub_success_sink.put.await_args
        assert call is not None
        assert call.kwargs["upload_metadata"] == {"product_id": 99, "description": "rear view"}

    @pytest.mark.usefixtures("stub_success_sink")
    async def test_capture_rejects_large_upload_metadata(self, client: AsyncClient) -> None:
        """Upload metadata should be bounded before capture or sink work starts."""
        resp = await client.post("/captures", json={"description": "x" * 5000})

        assert resp.status_code == 422

    async def test_capture_returns_507_when_upload_queue_is_full(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        stub_failing_sink: _StubSink,
        tmp_path: Path,
    ) -> None:
        """A failed upload should surface capacity exhaustion instead of growing the queue."""
        del stub_failing_sink
        original = settings.image_path
        original_max_entries = settings.upload_queue_max_pending_entries
        settings.image_path = tmp_path
        settings.upload_queue_max_pending_entries = 0
        cast("Any", camera_manager)._upload_queue = None

        try:
            resp = await client.post("/captures", json={"product_id": 7})
        finally:
            settings.image_path = original
            settings.upload_queue_max_pending_entries = original_max_entries
            cast("Any", camera_manager)._upload_queue = None

        assert resp.status_code == 507

    async def test_capture_runtime_error_returns_500(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RuntimeError during capture should surface as 500."""
        monkeypatch.setattr(camera_manager, "capture_jpeg", AsyncMock(side_effect=RuntimeError("boom")))
        resp = await client.post("/captures")
        assert resp.status_code == 500
