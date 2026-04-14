"""Tests for camera management dependencies and orchestration."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.camera import CameraMode
from relab_rpi_cam_models.stream import StreamMode

from app.api.dependencies import camera_management as camera_deps
from app.api.exceptions import ActiveStreamError
from app.api.schemas.camera_controls import (
    CameraControlsCapabilities,
    CameraControlsPatch,
    CameraControlsView,
    FocusControlRequest,
    FocusMode,
)
from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.camera_backend import CaptureResult, StreamingCameraBackend, StreamStartResult
from app.api.services.camera_manager import CameraControlsNotSupportedError, CameraManager
from app.api.services.stream_service import StreamService
from app.core.config import settings

YOUTUBE_PROVIDER = "youtube"
MOCK_CAMERA = "mock-camera"


class FakeBackend:
    """Small backend stub for camera-manager unit tests."""

    def __init__(self) -> None:
        self.current_mode: CameraMode | None = None
        self.cleanup = AsyncMock()
        self.stop_stream = AsyncMock()
        self.open = AsyncMock(side_effect=self._open)
        self.capture_image = AsyncMock()
        self.start_stream = AsyncMock()
        self.get_stream_metadata = AsyncMock(return_value=({"Model": "mock-camera"}, {"FrameDuration": 33_333}))

    async def _open(self, mode: CameraMode) -> None:
        self.current_mode = mode


@pytest.fixture
def mock_camera_manager(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Return a patched camera manager with async mocks."""
    mgr = SimpleNamespace(
        stream=SimpleNamespace(is_active=False, started_at=None),
        cleanup=AsyncMock(),
        stop_streaming=AsyncMock(),
    )
    monkeypatch.setattr(camera_deps, "camera_manager", mgr)
    return mgr


class TestCameraToStandby:
    """Tests for camera_to_standby."""

    async def test_cleans_up_when_inactive(self, mock_camera_manager: SimpleNamespace) -> None:
        """Should call cleanup if the stream is not active."""
        await camera_deps.camera_to_standby()
        mock_camera_manager.cleanup.assert_awaited_once()

    async def test_skips_cleanup_when_active(self, mock_camera_manager: SimpleNamespace) -> None:
        """Should not call cleanup if the stream is active."""
        mock_camera_manager.stream.is_active = True
        await camera_deps.camera_to_standby()
        mock_camera_manager.cleanup.assert_not_awaited()


class TestCheckStreamDuration:
    """Tests for check_stream_duration."""

    async def test_stops_overdue_stream(
        self,
        mock_camera_manager: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should stop the stream if it has been active longer than the configured maximum duration."""
        monkeypatch.setattr(settings, "max_stream_duration_s", 10)
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.stream.started_at = datetime.now(UTC) - timedelta(seconds=20)

        await camera_deps.check_stream_duration()

        mock_camera_manager.stop_streaming.assert_awaited_once()

    async def test_ignores_recent_stream(
        self,
        mock_camera_manager: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should not stop the stream if it has been active for less than the configured maximum duration."""
        monkeypatch.setattr(settings, "max_stream_duration_s", 10)
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.stream.started_at = datetime.now(UTC)

        await camera_deps.check_stream_duration()

        mock_camera_manager.stop_streaming.assert_not_awaited()

    async def test_logs_runtime_error(
        self,
        mock_camera_manager: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should log a RuntimeError if stopping the stream fails, but not raise it."""
        monkeypatch.setattr(settings, "max_stream_duration_s", 10)
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.stream.started_at = datetime.now(UTC) - timedelta(seconds=20)
        mock_camera_manager.stop_streaming.side_effect = RuntimeError("boom")

        await camera_deps.check_stream_duration()

        mock_camera_manager.stop_streaming.assert_awaited_once()


class TestCameraManagerCleanup:
    """Tests for CameraManager.cleanup method."""

    async def test_cleanup_uses_correct_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should call clear_directory with image_ttl_s."""
        mock_clear_directory = AsyncMock()
        monkeypatch.setattr("app.api.services.camera_manager.clear_directory", mock_clear_directory)

        manager = CameraManager(backend=cast("StreamingCameraBackend",FakeBackend()))

        await manager.cleanup()

        mock_clear_directory.assert_awaited_once()
        call_args = mock_clear_directory.call_args
        assert call_args[1]["time_to_live_s"] == settings.image_ttl_s


class TestCameraManagerStartStreaming:
    """Tests for CameraManager.start_streaming method."""

    async def test_raises_when_stream_already_active(self) -> None:
        """Should raise ActiveStreamError if a stream is already active."""
        manager = CameraManager(backend=cast("StreamingCameraBackend",FakeBackend()))
        manager.stream.mode = StreamMode.YOUTUBE
        manager.stream.started_at = datetime.now(UTC)

        with pytest.raises(ActiveStreamError):
            await manager.start_streaming(StreamMode.YOUTUBE)

    async def test_happy_path_youtube_streaming(self) -> None:
        """Should start a stream and expose provider-neutral stream state."""
        backend = FakeBackend()
        backend.start_stream.return_value = StreamStartResult(
            mode=StreamMode.YOUTUBE,
            url=AnyUrl("https://youtube.com/watch?v=valid-broadcast"),
        )
        manager = CameraManager(backend=cast("StreamingCameraBackend",backend))

        youtube_config = YoutubeStreamConfig(
            stream_key=SecretStr("valid-key"),
            broadcast_key=SecretStr("valid-broadcast"),
        )
        result = await manager.start_streaming(StreamMode.YOUTUBE, youtube_config=youtube_config)

        assert result.mode == StreamMode.YOUTUBE
        assert result.provider == YOUTUBE_PROVIDER
        assert manager.stream.is_active
        assert manager.stream_service.state.is_active
        backend.start_stream.assert_awaited_once_with(StreamMode.YOUTUBE, youtube_config=youtube_config)

    async def test_stream_start_failure_resets_state(self) -> None:
        """Should reset stream state if the backend fails to start the stream."""
        backend = FakeBackend()
        backend.start_stream.side_effect = RuntimeError("camera disconnected")
        manager = CameraManager(backend=cast("StreamingCameraBackend",backend))

        youtube_config = YoutubeStreamConfig(
            stream_key=SecretStr("key"),
            broadcast_key=SecretStr("broadcast"),
        )

        with pytest.raises(RuntimeError, match="camera disconnected"):
            await manager.start_streaming(StreamMode.YOUTUBE, youtube_config=youtube_config)

        assert not manager.stream.is_active

    async def test_requires_youtube_config(self) -> None:
        """Should bubble up provider-specific validation errors from the backend."""
        backend = FakeBackend()
        backend.start_stream.side_effect = YoutubeConfigRequiredError
        manager = CameraManager(backend=cast("StreamingCameraBackend",backend))

        with pytest.raises(YoutubeConfigRequiredError):
            await manager.start_streaming(StreamMode.YOUTUBE, youtube_config=None)


class TestCameraManagerCapture:
    """Tests for CameraManager capture flows."""

    async def test_capture_uses_backend_result(self, tmp_path: Path) -> None:
        """Should build an image response from the backend capture result."""
        backend = FakeBackend()
        image = Image.new("RGB", (64, 64), color="green")
        backend.capture_image.return_value = CaptureResult(
            image=image,
            camera_properties={"Model": "mock-camera"},
            capture_metadata={"FrameDuration": 33_333},
        )
        manager = CameraManager(backend=cast("StreamingCameraBackend",backend))
        original = settings.image_path
        settings.image_path = tmp_path / "images"
        settings.image_path.mkdir()

        try:
            response = await manager.capture_jpeg()
        finally:
            settings.image_path = original

        assert response.metadata.camera_properties.camera_model == MOCK_CAMERA
        backend.capture_image.assert_awaited_once()


class TestCameraManagerStopStreaming:
    """Tests for CameraManager.stop_streaming method."""

    async def test_raises_when_no_stream_active(self) -> None:
        """Should raise RuntimeError if no stream is active."""
        manager = CameraManager(backend=cast("StreamingCameraBackend",FakeBackend()))

        with pytest.raises(RuntimeError, match="No stream active"):
            await manager.stop_streaming()

    async def test_stops_active_stream(self) -> None:
        """Should stop the stream through the backend."""
        backend = FakeBackend()
        manager = CameraManager(backend=cast("StreamingCameraBackend",backend))
        manager.stream.mode = StreamMode.YOUTUBE
        manager.stream.url = AnyUrl("https://youtube.com/watch?v=valid-broadcast")
        manager.stream.started_at = datetime.now(UTC)

        await manager.stop_streaming()

        backend.stop_stream.assert_awaited_once()
        assert not manager.stream.is_active


class TestCameraManagerGetStatus:
    """Tests for CameraManager.get_status method."""

    async def test_returns_status_without_stream(self) -> None:
        """Should return status with no stream info when stream is inactive."""
        manager = CameraManager(backend=cast("StreamingCameraBackend",FakeBackend()))

        status = await manager.get_status()

        assert status.current_mode is None
        assert status.stream is None


class TestCameraManagerControls:
    """Tests for CameraManager control dispatch."""

    async def test_raises_for_backend_without_controls(self) -> None:
        """Should reject controls operations when the backend does not implement them."""
        manager = CameraManager(backend=cast("StreamingCameraBackend", FakeBackend()))

        with pytest.raises(CameraControlsNotSupportedError):
            await manager.get_controls()

    async def test_get_controls_dispatches_to_backend(self) -> None:
        """Should return control capabilities from a controllable backend."""
        backend = FakeBackend()
        backend.get_controls = AsyncMock(return_value=CameraControlsView(supported=True))  # type: ignore[attr-defined]
        backend.get_controls_capabilities = AsyncMock(  # type: ignore[attr-defined]
            return_value=CameraControlsCapabilities(supported=True)
        )
        backend.set_controls = AsyncMock()  # type: ignore[attr-defined]
        backend.set_focus = AsyncMock()  # type: ignore[attr-defined]
        manager = CameraManager(backend=backend)

        result = await manager.get_controls()

        assert result.supported is True
        backend.get_controls.assert_awaited_once()

    async def test_set_controls_dispatches_to_backend(self) -> None:
        """Should apply generic controls through a controllable backend."""
        backend = FakeBackend()
        backend.get_controls = AsyncMock()  # type: ignore[attr-defined]
        backend.get_controls_capabilities = AsyncMock(  # type: ignore[attr-defined]
            return_value=CameraControlsCapabilities(supported=True)
        )
        backend.set_controls = AsyncMock(return_value=CameraControlsView(supported=True))  # type: ignore[attr-defined]
        backend.set_focus = AsyncMock()  # type: ignore[attr-defined]
        manager = CameraManager(backend=backend)
        patch = CameraControlsPatch(controls={"ExposureTime": 10000})

        result = await manager.set_controls(patch)

        assert result.supported is True
        backend.set_controls.assert_awaited_once_with({"ExposureTime": 10000})

    async def test_set_focus_dispatches_to_backend(self) -> None:
        """Should apply friendly focus controls through a controllable backend."""
        backend = FakeBackend()
        backend.get_controls = AsyncMock()  # type: ignore[attr-defined]
        backend.get_controls_capabilities = AsyncMock(  # type: ignore[attr-defined]
            return_value=CameraControlsCapabilities(supported=True)
        )
        backend.set_controls = AsyncMock()  # type: ignore[attr-defined]
        backend.set_focus = AsyncMock(return_value=CameraControlsView(supported=True))  # type: ignore[attr-defined]
        manager = CameraManager(backend=backend)
        request = FocusControlRequest(mode=FocusMode.CONTINUOUS)

        result = await manager.set_focus(request)

        assert result.supported is True
        backend.set_focus.assert_awaited_once_with(request)

    async def test_get_controls_capabilities_dispatches_to_backend(self) -> None:
        """Should return UI-friendly capabilities from a controllable backend."""
        backend = FakeBackend()
        backend.get_controls = AsyncMock()  # type: ignore[attr-defined]
        backend.set_controls = AsyncMock()  # type: ignore[attr-defined]
        backend.set_focus = AsyncMock()  # type: ignore[attr-defined]
        backend.get_controls_capabilities = AsyncMock(  # type: ignore[attr-defined]
            return_value=CameraControlsCapabilities(supported=True)
        )
        manager = CameraManager(backend=backend)

        result = await manager.get_controls_capabilities()

        assert result.supported is True
        backend.get_controls_capabilities.assert_awaited_once()


class TestStreamService:
    """Tests for focused stream state orchestration."""

    def test_start_populates_state(self) -> None:
        """Starting a stream should populate stream state."""
        service = StreamService()
        service.start(
            StreamStartResult(
                mode=StreamMode.YOUTUBE,
                url=AnyUrl("https://youtube.com/watch?v=valid-broadcast"),
            )
        )

        assert service.state.is_active
        assert service.state.mode == StreamMode.YOUTUBE

    def test_reset_clears_state(self) -> None:
        """Reset should clear active stream state."""
        service = StreamService()
        service.start(
            StreamStartResult(
                mode=StreamMode.YOUTUBE,
                url=AnyUrl("https://youtube.com/watch?v=valid-broadcast"),
            )
        )

        service.reset()

        assert not service.state.is_active

    def test_build_view_returns_contract_view(self) -> None:
        """The service should build the public stream view from runtime state."""
        service = StreamService()
        service.start(
            StreamStartResult(
                mode=StreamMode.YOUTUBE,
                url=AnyUrl("https://youtube.com/watch?v=valid-broadcast"),
            )
        )

        view = service.build_view({"Model": MOCK_CAMERA}, {"FrameDuration": 33_333})

        assert view is not None
        assert view.mode == StreamMode.YOUTUBE
