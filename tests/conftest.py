"""Shared fixtures for the RPi camera plugin test suite."""

from collections.abc import AsyncGenerator
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pydantic import AnyUrl
from relab_rpi_cam_models.camera import CameraMode
from relab_rpi_cam_models.stream import StreamMode

from app.api.dependencies.auth import reload_authorized_hashes, verify_request
from app.api.dependencies.camera_management import get_camera_manager
from app.api.schemas.camera_controls import (
    CameraControlInfo,
    CameraControlsCapabilities,
    CameraControlsView,
    FocusControlRequest,
    JsonValue,
)
from app.api.schemas.streaming import YoutubeStreamConfig
from app.api.services.camera_backend import CaptureResult, StreamingCameraBackend, StreamStartResult
from app.api.services.camera_manager import CameraManager
from app.core.config import settings
from app.main import app

TEST_API_KEY = "test-api-key-12345"

# Add test API key to authorized keys for cookie auth tests
if TEST_API_KEY not in settings.authorized_api_keys:
    settings.authorized_api_keys.append(TEST_API_KEY)
    reload_authorized_hashes()


class FakeBackend:
    """Small backend stub for integration tests."""

    def __init__(self) -> None:
        self.current_mode: CameraMode | None = None
        self.cleaned_up = False
        self.stream_active = False
        self.stream_url = AnyUrl("https://youtube.com/watch?v=TEST_BROADCAST_KEY_123")
        self.image = Image.new("RGB", (100, 100), color="red")
        self.camera_properties = {"Model": "mock-camera"}
        self.capture_metadata = {"FrameDuration": 33333}
        self.last_youtube_config: YoutubeStreamConfig | None = None
        self.controls: dict[str, JsonValue] = {}

    async def open(self, mode: CameraMode) -> None:
        """Open the fake backend."""
        self.current_mode = mode

    async def capture_image(self) -> CaptureResult:
        """Capture a fake still image."""
        self.current_mode = CameraMode.PHOTO
        return CaptureResult(
            image=self.image,
            camera_properties=self.camera_properties,
            capture_metadata=self.capture_metadata,
        )

    async def start_stream(
        self,
        mode: StreamMode,
        *,
        youtube_config: YoutubeStreamConfig | None = None,
    ) -> StreamStartResult:
        """Start a fake stream."""
        self.current_mode = CameraMode.VIDEO
        self.stream_active = True
        self.last_youtube_config = youtube_config
        return StreamStartResult(mode=mode, url=self.stream_url)

    async def stop_stream(self) -> None:
        """Stop a fake stream."""
        self.stream_active = False

    async def get_stream_metadata(self) -> tuple[dict[str, str], dict[str, int]]:
        """Return mock camera metadata."""
        return self.camera_properties, self.capture_metadata

    async def get_controls(self) -> CameraControlsView:
        """Return mock control capabilities."""
        return CameraControlsView(
            supported=True,
            controls={
                "AfMode": CameraControlInfo(
                    name="AfMode",
                    namespace="fake",
                    value_type="enum",
                    options=["manual", "auto", "continuous"],
                )
            },
            values=self.capture_metadata,
        )

    async def get_controls_capabilities(self) -> CameraControlsCapabilities:
        """Return mock control capabilities for UI helpers."""
        view = await self.get_controls()
        return CameraControlsCapabilities(
            supported=True,
            controls=list(view.controls.values()),
        )

    async def set_controls(self, controls: dict[str, JsonValue]) -> CameraControlsView:
        """Store and return mock controls."""
        self.controls.update(controls)
        return await self.get_controls()

    async def set_focus(self, request: FocusControlRequest) -> CameraControlsView:
        """Store and return mock focus controls."""
        self.controls["FocusMode"] = request.mode
        if request.lens_position is not None:
            self.controls["LensPosition"] = request.lens_position
        return await self.get_controls()

    async def cleanup(self) -> None:
        """Release mock resources."""
        self.cleaned_up = True
        self.current_mode = None


@pytest.fixture
def camera_manager() -> CameraManager:
    """Return a CameraManager with a fake provider-neutral backend."""
    backend = FakeBackend()
    return CameraManager(backend=cast("StreamingCameraBackend", backend))


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
