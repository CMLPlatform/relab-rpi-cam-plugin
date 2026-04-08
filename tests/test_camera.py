"""Tests for camera management endpoints."""

from httpx import AsyncClient

from app.api.services.camera_manager import CameraManager


class TestCameraStatus:
    """Tests for GET /camera/status."""

    async def test_status_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/camera/status")
        assert resp.status_code == 200

    async def test_status_default_fields(self, client: AsyncClient) -> None:
        resp = await client.get("/camera/status")
        data = resp.json()
        assert "current_mode" in data
        assert "stream" in data

    async def test_status_idle_by_default(self, client: AsyncClient) -> None:
        resp = await client.get("/camera/status")
        data = resp.json()
        assert data["current_mode"] is None
        assert data["stream"] is None


class TestCameraClose:
    """Tests for POST /camera/close."""

    async def test_close_idle_camera(self, client: AsyncClient) -> None:
        resp = await client.post("/camera/close")
        assert resp.status_code == 200

    async def test_close_returns_status(self, client: AsyncClient) -> None:
        resp = await client.post("/camera/close")
        data = resp.json()
        assert "current_mode" in data
        assert "stream" in data

    async def test_close_while_streaming_returns_409(
        self, client: AsyncClient, camera_manager: CameraManager
    ) -> None:
        camera_manager.stream.mode = "local"
        resp = await client.post("/camera/close")
        assert resp.status_code == 409
