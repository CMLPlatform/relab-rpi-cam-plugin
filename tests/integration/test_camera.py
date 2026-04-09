"""Tests for camera status endpoints."""

from httpx import AsyncClient

CURRENT_MODE_KEY = "current_mode"
STREAM_KEY = "stream"


class TestCameraStatus:
    """Tests for GET /camera."""

    async def test_status_returns_200(self, client: AsyncClient) -> None:
        """Test that the camera status endpoint returns a 200 response."""
        resp = await client.get("/camera")
        assert resp.status_code == 200

    async def test_status_default_fields(self, client: AsyncClient) -> None:
        """Test that the camera status response contains the expected fields even when the camera is idle."""
        resp = await client.get("/camera")
        data = resp.json()
        assert CURRENT_MODE_KEY in data
        assert STREAM_KEY in data

    async def test_status_idle_by_default(self, client: AsyncClient) -> None:
        """Test that the camera status shows the camera as idle (current_mode=None, stream=None) by default."""
        resp = await client.get("/camera")
        data = resp.json()
        assert data["current_mode"] is None
        assert data["stream"] is None
