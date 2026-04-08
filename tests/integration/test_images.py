"""Tests for image capture and retrieval endpoints."""

from pathlib import Path
from typing import Any, cast

import pytest
from httpx import AsyncClient
from PIL import Image
from relab_rpi_cam_models.camera import CameraMode

from app.api.routers import images as images_router
from app.api.services.camera_manager import CameraManager
from app.api.services.hardware_stubs import Picamera2Stub
from app.core.config import settings
from tests.constants import JPEG_CONTENT_TYPE

IMAGE_ID_KEY = "image_id"
IMAGE_URL_KEY = "image_url"
EXPIRES_AT_KEY = "expires_at"


class MockPicamera2(Picamera2Stub):
    """Typed camera stub for image endpoint tests."""

    def __init__(self, image: Image.Image, metadata: dict[str, int]) -> None:
        self._image = image
        self._metadata = metadata
        self.camera_properties: dict[str, str] = {"Model": "mock"}

    def capture_image(self) -> Image.Image:
        """Return the preset image."""
        return self._image

    def capture_metadata(self) -> dict[str, int] | None:
        """Return the preset metadata."""
        return self._metadata

    def configure(self, config: object) -> None:
        """Mock configure does nothing."""

    def start(self) -> None:
        """Mock start does nothing."""

    def stop(self) -> None:
        """Mock stop does nothing."""

    def close(self) -> None:
        """Mock close does nothing."""


class TestGetImage:
    """Tests for GET /images/{image_id}."""

    async def test_missing_image_returns_404(self, client: AsyncClient) -> None:
        """Test that requesting a nonexistent image ID returns a 404."""
        resp = await client.get("/images/nonexistent-id")
        assert resp.status_code == 404

    async def test_existing_image_returns_jpeg(self, client: AsyncClient, tmp_path: Path) -> None:
        """Test that requesting an existing image ID returns the JPEG file."""
        # Point settings to tmp dir and create a fake JPEG
        original = settings.image_path
        settings.image_path = tmp_path
        (tmp_path / "abc123.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        try:
            resp = await client.get("/images/abc123")
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


class TestCaptureEndpoint:
    """Tests for POST /images."""

    async def test_capture_returns_201(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        tmp_path: Path,
    ) -> None:
        """Test that capturing an image returns a 201 with the image ID and URL."""
        original = settings.image_path
        settings.image_path = tmp_path

        # Create a real PIL image that the mock camera will return
        pil_img = Image.new("RGB", (100, 100), color="red")
        camera_manager.camera = MockPicamera2(pil_img, {"FrameDuration": 33333})
        camera_manager.current_mode = CameraMode.PHOTO

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
        camera_manager: CameraManager,
        tmp_path: Path,
    ) -> None:
        """Test that capturing an image saves the JPEG file to disk."""
        original = settings.image_path
        settings.image_path = tmp_path

        pil_img = Image.new("RGB", (100, 100), color="blue")
        camera_manager.camera = MockPicamera2(pil_img, {"FrameDuration": 33333})
        camera_manager.current_mode = CameraMode.PHOTO

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


class TestMjpegGenerator:
    """Tests for the MJPEG stream generator."""

    async def test_generator_yields_frame_and_stops_on_error(self) -> None:
        """Test that the generator yields a frame and then stops when the preview manager raises a RuntimeError."""
        frames = [b"frame-bytes"]

        class _PreviewManager:
            async def capture_preview_jpeg(self) -> bytes:
                if frames:
                    return frames.pop()
                msg = "stop"
                raise RuntimeError(msg)

        gen = images_router._mjpeg_generator(cast("Any", _PreviewManager()))  # noqa: SLF001
        frame = await anext(gen)
        assert frame.startswith(b"--frame\r\n")
        with pytest.raises(StopAsyncIteration):
            await anext(gen)
