"""Tests for image capture and preview endpoints."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode

from app.api.services import camera_manager as camera_manager_mod
from app.api.services.camera_manager import CameraManager
from app.core.config import settings
from app.utils.backend_client import BackendUploadError, UploadedImageInfo
from tests.constants import JPEG_CONTENT_TYPE

CAPTURED_STATUS_KEY = "status"
IMAGE_ID_KEY = "image_id"
IMAGE_URL_KEY = "image_url"
CONFLICT_RESPONSE_CODE = "409"


@pytest.fixture
def fake_upload_success(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace camera_manager's imported upload_image with a successful AsyncMock."""
    mock = AsyncMock(
        return_value=UploadedImageInfo(
            image_id="a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8",
            image_url=AnyUrl("https://backend.example/images/a1b2c3d4.jpg"),
        ),
    )
    monkeypatch.setattr(camera_manager_mod, "upload_image", mock)
    return mock


@pytest.fixture
def fake_upload_failure(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace camera_manager's imported upload_image with a failing AsyncMock."""
    mock = AsyncMock(side_effect=BackendUploadError("network unreachable"))
    monkeypatch.setattr(camera_manager_mod, "upload_image", mock)
    return mock


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
    """Tests for POST /images — synchronous push + queue fallback."""

    async def test_capture_pushes_to_backend_and_returns_uploaded_status(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        fake_upload_success: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """Happy path: synchronous push returns status=uploaded with backend URL."""
        original = settings.image_path
        settings.image_path = tmp_path
        camera_manager.upload_queue = camera_manager_mod.UploadQueue(tmp_path / "queue")

        try:
            resp = await client.post("/images", json={"product_id": 1})
            assert resp.status_code == 201
            data = resp.json()
            assert data[CAPTURED_STATUS_KEY] == "uploaded"
            assert data[IMAGE_ID_KEY] == "a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8"
            assert data[IMAGE_URL_KEY] == "https://backend.example/images/a1b2c3d4.jpg"
        finally:
            settings.image_path = original

        assert fake_upload_success.await_count == 1

    async def test_capture_deletes_local_file_after_upload(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        fake_upload_success: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """After a successful push the local JPEG should be gone — single source of truth."""
        original = settings.image_path
        settings.image_path = tmp_path
        camera_manager.upload_queue = camera_manager_mod.UploadQueue(tmp_path / "queue")

        try:
            resp = await client.post("/images", json=None)
            assert resp.status_code == 201
            remaining = list(tmp_path.glob("*.jpg"))
            assert remaining == []
        finally:
            settings.image_path = original

    async def test_capture_queues_on_upload_failure(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        fake_upload_failure: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """A failing upload should enqueue the capture and return status=queued."""
        original = settings.image_path
        settings.image_path = tmp_path
        camera_manager.upload_queue = camera_manager_mod.UploadQueue(tmp_path / "queue")

        try:
            resp = await client.post("/images", json={"product_id": 7})
            assert resp.status_code == 201
            data = resp.json()
            assert data[CAPTURED_STATUS_KEY] == "queued"
            assert data[IMAGE_URL_KEY] is None
        finally:
            settings.image_path = original

        assert fake_upload_failure.await_count == 1
        # Queued file lives under data/queue/ with a .json sidecar.
        queue_root = tmp_path / "queue"
        jpgs = list(queue_root.glob("*.jpg"))
        jsons = list(queue_root.glob("*.json"))
        assert len(jpgs) == 1
        assert len(jsons) == 1

    async def test_capture_forwards_upload_metadata_to_backend_client(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        fake_upload_success: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """The upload_metadata body should arrive verbatim at backend_client.upload_image."""
        original = settings.image_path
        settings.image_path = tmp_path
        camera_manager.upload_queue = camera_manager_mod.UploadQueue(tmp_path / "queue")

        try:
            resp = await client.post(
                "/images",
                json={"product_id": 99, "description": "rear view"},
            )
            assert resp.status_code == 201
        finally:
            settings.image_path = original

        call = fake_upload_success.await_args
        assert call is not None
        assert call.kwargs["upload_metadata"] == {"product_id": 99, "description": "rear view"}

    async def test_capture_runtime_error_returns_500(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RuntimeError during capture should surface as 500."""

        async def _boom(upload_metadata: object = None) -> object:  # noqa: ARG001
            msg = "boom"
            raise RuntimeError(msg)

        monkeypatch.setattr(camera_manager, "capture_jpeg", _boom)
        resp = await client.post("/images")
        assert resp.status_code == 500
