"""Tests for camera management endpoints."""

from httpx import AsyncClient
from relab_rpi_cam_models.stream import StreamMode

from app.api.services.camera_manager import CameraManager

CURRENT_MODE_KEY = "current_mode"
STREAM_KEY = "stream"


class TestCameraOpen:
    """Tests for POST /camera/open."""

    async def test_open_camera_returns_status(self, client: AsyncClient) -> None:
        """Test that opening the camera returns the current status."""
        resp = await client.post("/camera/open")
        assert resp.status_code == 200
        data = resp.json()
        assert CURRENT_MODE_KEY in data

    async def test_open_camera_video_mode(self, client: AsyncClient) -> None:
        """Test that opening the camera in video mode sets the stream mode to LOCAL."""
        resp = await client.post("/camera/open", params={"mode": "video"})
        assert resp.status_code == 200


class TestCameraStatus:
    """Tests for GET /camera/status."""

    async def test_status_returns_200(self, client: AsyncClient) -> None:
        """Test that the camera status endpoint returns a 200 response."""
        resp = await client.get("/camera/status")
        assert resp.status_code == 200

    async def test_status_default_fields(self, client: AsyncClient) -> None:
        """Test that the camera status response contains the expected fields even when the camera is idle."""
        resp = await client.get("/camera/status")
        data = resp.json()
        assert CURRENT_MODE_KEY in data
        assert STREAM_KEY in data

    async def test_status_idle_by_default(self, client: AsyncClient) -> None:
        """Test that the camera status shows the camera as idle (current_mode=None, stream=None) by default."""
        resp = await client.get("/camera/status")
        data = resp.json()
        assert data["current_mode"] is None
        assert data["stream"] is None


class TestCameraClose:
    """Tests for POST /camera/close."""

    async def test_close_idle_camera(self, client: AsyncClient) -> None:
        """Test that closing the camera when it's idle returns a 200 response and doesn't cause errors."""
        resp = await client.post("/camera/close")
        assert resp.status_code == 200

    async def test_close_returns_status(self, client: AsyncClient) -> None:
        """Test that closing the camera returns the current status."""
        resp = await client.post("/camera/close")
        data = resp.json()
        assert CURRENT_MODE_KEY in data
        assert STREAM_KEY in data

    async def test_close_while_streaming_returns_409(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Test that attempting to close the camera while a stream is active returns a 409 Conflict response."""
        camera_manager.stream.mode = StreamMode.LOCAL
        resp = await client.post("/camera/close")
        assert resp.status_code == 409
