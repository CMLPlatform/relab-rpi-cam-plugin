"""Tests for image capture and retrieval endpoints."""

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient
from relab_rpi_cam_models.stream import StreamMode

from app.api.services.camera_manager import CameraManager
from app.core.config import settings
from tests.constants import JPEG_CONTENT_TYPE

IMAGE_ID_KEY = "image_id"
IMAGE_URL_KEY = "image_url"
EXPIRES_AT_KEY = "expires_at"
CONFLICT_RESPONSE_CODE = "409"


class TestGetImage:
    """Tests for GET /images/{image_id}."""

    async def test_missing_image_returns_404(self, client: AsyncClient) -> None:
        """Test that requesting a nonexistent image ID returns a 404."""
        # Use a valid hex ID format (32 hex chars)
        resp = await client.get("/images/abcdef0123456789abcdef0123456789")
        assert resp.status_code == 404

    async def test_existing_image_returns_jpeg(self, client: AsyncClient, tmp_path: Path) -> None:
        """Test that requesting an existing image ID returns the JPEG file."""
        # Use a valid hex ID format (32 hex chars)
        valid_image_id = "abcdef0123456789abcdef0123456789"
        # Point settings to tmp dir and create a fake JPEG
        original = settings.image_path
        settings.image_path = tmp_path
        (tmp_path / f"{valid_image_id}.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        try:
            resp = await client.get(f"/images/{valid_image_id}")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == JPEG_CONTENT_TYPE
        finally:
            settings.image_path = original


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

    async def test_preview_while_streaming_returns_409(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Preview should be unavailable while a stream is active."""
        camera_manager.stream.mode = StreamMode.YOUTUBE
        resp = await client.get("/images/preview")
        assert resp.status_code == 409

    async def test_openapi_documents_409_response(self, client: AsyncClient) -> None:
        """OpenAPI should document preview conflicts while streaming."""
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        preview_get = resp.json()["paths"]["/images/preview"]["get"]
        assert CONFLICT_RESPONSE_CODE in preview_get["responses"]


class TestCaptureEndpoint:
    """Tests for POST /images."""

    async def test_capture_returns_201(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Test that capturing an image returns a 201 with the image ID and URL."""
        original = settings.image_path
        settings.image_path = tmp_path

        try:
            resp = await client.post("/images")
            assert resp.status_code == 201
            data = resp.json()
            assert IMAGE_ID_KEY in data
            assert IMAGE_URL_KEY in data
            assert EXPIRES_AT_KEY in data
        finally:
            settings.image_path = original

    async def test_capture_saves_file_to_disk(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Test that capturing an image saves the JPEG file to disk."""
        original = settings.image_path
        settings.image_path = tmp_path

        try:
            resp = await client.post("/images")
            image_id = resp.json()[IMAGE_ID_KEY]
            assert (tmp_path / f"{image_id}.jpg").exists()
        finally:
            settings.image_path = original

    async def test_capture_runtime_error_returns_500(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that if capturing the image raises a RuntimeError, a 500 response is returned."""

        async def _boom() -> object:
            msg = "boom"
            raise RuntimeError(msg)

        monkeypatch.setattr(camera_manager, "capture_jpeg", _boom)
        resp = await client.post("/images")
        assert resp.status_code == 500
