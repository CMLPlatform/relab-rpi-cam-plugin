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


class TestCameraControls:
    """Tests for camera controls endpoints."""

    async def test_controls_returns_200(self, client: AsyncClient) -> None:
        """Test that the camera controls endpoint returns discovered controls."""
        resp = await client.get("/camera/controls")

        assert resp.status_code == 200
        data = resp.json()
        assert data["supported"] is True
        assert data["controls"]["AfMode"]["options"] == ["manual", "auto", "continuous"]

    async def test_set_controls_returns_200(self, client: AsyncClient) -> None:
        """Test that backend-native camera controls can be patched."""
        resp = await client.patch("/camera/controls", json={"controls": {"ExposureTime": 10000}})

        assert resp.status_code == 200
        assert resp.json()["supported"] is True

    async def test_set_focus_returns_200(self, client: AsyncClient) -> None:
        """Test that friendly focus controls can be applied."""
        resp = await client.put("/camera/focus", json={"mode": "manual", "lens_position": 1.5})

        assert resp.status_code == 200
        assert resp.json()["supported"] is True
