"""Tests for camera status endpoints."""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.api.services.camera_manager import CameraControlsNotSupportedError, CameraManager

CURRENT_MODE_KEY = "current_mode"
LAST_IMAGE_URL_KEY = "last_image_url"
STREAM_KEY = "stream"
JPEG_MAGIC_PREFIX = b"\xff\xd8"


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
        assert LAST_IMAGE_URL_KEY in data
        assert STREAM_KEY in data

    async def test_status_idle_by_default(self, client: AsyncClient) -> None:
        """Test that the camera status shows the camera as idle (current_mode=None, stream=None) by default."""
        resp = await client.get("/camera")
        data = resp.json()
        assert data["current_mode"] is None
        assert data["stream"] is None
        assert data["last_image_url"] is None

    async def test_snapshot_returns_jpeg(self, client: AsyncClient) -> None:
        """Test that the snapshot endpoint returns a JPEG preview frame."""
        resp = await client.get("/preview/snapshot")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/jpeg")
        assert resp.content[:2] == JPEG_MAGIC_PREFIX


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

class TestCameraControlsNotSupported:
    """Controls endpoints should surface a 501 when the backend can't implement them."""

    async def test_get_controls_returns_501_when_backend_not_controllable(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``GET /camera/controls`` -> 501 when the backend raises CameraControlsNotSupportedError."""
        monkeypatch.setattr(
            camera_manager,
            "get_controls",
            AsyncMock(side_effect=CameraControlsNotSupportedError(camera_manager.backend)),
        )
        resp = await client.get("/camera/controls")
        assert resp.status_code == 501

    async def test_set_controls_returns_501_when_backend_not_controllable(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``PATCH /camera/controls`` -> 501 when the backend cannot apply them."""
        monkeypatch.setattr(
            camera_manager,
            "set_controls",
            AsyncMock(side_effect=CameraControlsNotSupportedError(camera_manager.backend)),
        )
        resp = await client.patch("/camera/controls", json={"controls": {"ExposureTime": 10000}})
        assert resp.status_code == 501

    async def test_set_controls_returns_422_on_value_error(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``PATCH /camera/controls`` -> 422 when the backend rejects the payload."""
        monkeypatch.setattr(
            camera_manager,
            "set_controls",
            AsyncMock(side_effect=ValueError("ExposureTime out of range")),
        )
        resp = await client.patch("/camera/controls", json={"controls": {"ExposureTime": -1}})
        assert resp.status_code == 422

    async def test_set_focus_returns_501_when_backend_not_controllable(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``PUT /camera/focus`` -> 501 when the backend cannot apply focus."""
        monkeypatch.setattr(
            camera_manager,
            "set_focus",
            AsyncMock(side_effect=CameraControlsNotSupportedError(camera_manager.backend)),
        )
        resp = await client.put("/camera/focus", json={"mode": "manual", "lens_position": 1.5})
        assert resp.status_code == 501

    async def test_set_focus_returns_422_on_value_error(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``PUT /camera/focus`` -> 422 when the backend rejects the focus request."""
        monkeypatch.setattr(
            camera_manager,
            "set_focus",
            AsyncMock(side_effect=ValueError("lens_position out of range")),
        )
        resp = await client.put("/camera/focus", json={"mode": "manual", "lens_position": 99.0})
        assert resp.status_code == 422
