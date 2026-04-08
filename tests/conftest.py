"""Shared fixtures for the RPi camera plugin test suite."""

from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import verify_request
from app.api.dependencies.camera_management import get_camera_manager
from app.api.services.camera_manager import CameraManager
from app.core.config import settings
from app.main import app

TEST_API_KEY = "test-api-key-12345"

# Add test API key to authorized keys for cookie auth tests
if TEST_API_KEY not in settings.authorized_api_keys:
    settings.authorized_api_keys.append(TEST_API_KEY)


@pytest.fixture
def camera_manager() -> CameraManager:
    """Return a CameraManager with a mocked Picamera2 backend."""
    mgr = CameraManager()
    mgr.camera = MagicMock()
    mgr.camera.camera_properties = {"Model": "mock-camera"}
    mgr.camera.capture_metadata = MagicMock(return_value={"FrameDuration": 33333})
    mgr.camera.capture_image = MagicMock()
    return mgr


@pytest.fixture
async def client(camera_manager: CameraManager) -> AsyncGenerator[AsyncClient]:
    """Async test client with auth and camera manager dependencies overridden."""

    async def _override_auth() -> str:
        return TEST_API_KEY

    app.dependency_overrides[verify_request] = _override_auth
    app.dependency_overrides[get_camera_manager] = lambda: camera_manager

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def unauthed_client() -> AsyncGenerator[AsyncClient]:
    """Async test client without auth override (requests will be rejected)."""
    app.dependency_overrides.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
