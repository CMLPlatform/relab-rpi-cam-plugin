"""Tests for camera management dependencies and orchestration."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
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
from tests.constants import EXAMPLE_IMAGE_URL, YOUTUBE_WATCH_URL_PREFIX

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

YOUTUBE_PROVIDER = "youtube"
MOCK_CAMERA = "mock-camera"


class FakeBackend:
    """Small backend stub for camera-manager unit tests."""

    def __init__(self) -> None:
        self.current_mode: CameraMode | None = None
        self.cleanup: Any = AsyncMock()
        self.stop_stream: Any = AsyncMock()
        self.open: Any = AsyncMock(side_effect=self._open)
        self.capture_image: Any = AsyncMock()
        self.start_stream: Any = AsyncMock()
        self.get_stream_metadata: Any = AsyncMock(return_value=({"Model": "mock-camera"}, {"FrameDuration": 33_333}))
        # Optional controllable-backend hooks. Declare them as attributes so
        # tests can dynamically attach AsyncMock instances without causing
        # static analysis unresolved-attribute errors. They default to None
        # which preserves runtime behaviour for tests that expect a
        # non-controllable backend.
        self.get_controls: Callable[[], Awaitable[CameraControlsView]] | None = None
        self.get_controls_capabilities: Callable[[], Awaitable[CameraControlsCapabilities]] | None = None
        self.set_controls: Callable[[dict[str, Any]], Awaitable[CameraControlsView]] | None = None
        self.set_focus: Callable[[FocusControlRequest], Awaitable[CameraControlsView]] | None = None

    @property
    def camera(self) -> None:
        """Return None for fake backend (no hardware camera)."""
        return None

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


class TestCheckStreamHealth:
    """Tests for check_stream_health — the liveness probe for the active stream."""

    async def test_noop_when_no_stream_active(
        self,
        mock_camera_manager: SimpleNamespace,
    ) -> None:
        """No active stream: the probe returns immediately without touching anything."""
        mock_camera_manager.stream.is_active = False
        mock_camera_manager.get_stream_info = AsyncMock()

        await camera_deps.check_stream_health()

        mock_camera_manager.get_stream_info.assert_not_awaited()
        mock_camera_manager.stop_streaming.assert_not_awaited()

    async def test_stops_stream_when_get_info_returns_none(
        self,
        mock_camera_manager: SimpleNamespace,
    ) -> None:
        """Get-info reports no info (e.g. ffmpeg died) -> stop the stream for recovery."""
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.get_stream_info = AsyncMock(return_value=None)

        await camera_deps.check_stream_health()

        mock_camera_manager.get_stream_info.assert_awaited_once()
        mock_camera_manager.stop_streaming.assert_awaited_once()

    async def test_tolerates_os_error_and_stops_stream(
        self,
        mock_camera_manager: SimpleNamespace,
    ) -> None:
        """OSError from get_stream_info should trigger a recovery stop."""
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.get_stream_info = AsyncMock(side_effect=OSError("pipe closed"))

        await camera_deps.check_stream_health()

        mock_camera_manager.stop_streaming.assert_awaited_once()

    async def test_tolerates_runtime_error_and_stops_stream(
        self,
        mock_camera_manager: SimpleNamespace,
    ) -> None:
        """RuntimeError from get_stream_info should also trigger a recovery stop."""
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.get_stream_info = AsyncMock(side_effect=RuntimeError("crashed"))

        await camera_deps.check_stream_health()

        mock_camera_manager.stop_streaming.assert_awaited_once()

    async def test_suppresses_error_from_recovery_stop(
        self,
        mock_camera_manager: SimpleNamespace,
    ) -> None:
        """If the recovery stop itself raises RuntimeError, the probe still returns cleanly."""
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.get_stream_info = AsyncMock(side_effect=RuntimeError("crashed"))
        mock_camera_manager.stop_streaming.side_effect = RuntimeError("cannot stop")

        # Should not raise — contextlib.suppress handles it.
        await camera_deps.check_stream_health()

        mock_camera_manager.stop_streaming.assert_awaited_once()

    async def test_healthy_stream_leaves_state_alone(
        self,
        mock_camera_manager: SimpleNamespace,
    ) -> None:
        """Healthy get_stream_info result: do not stop the stream."""
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.get_stream_info = AsyncMock(return_value=SimpleNamespace(healthy=True))

        await camera_deps.check_stream_health()

        mock_camera_manager.stop_streaming.assert_not_awaited()


class TestCameraManagerCleanup:
    """Tests for CameraManager.cleanup method."""

    async def test_cleanup_uses_correct_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should call clear_directory with image_ttl_s."""
        mock_clear_directory = AsyncMock()
        monkeypatch.setattr("app.api.services.camera_manager.clear_directory", mock_clear_directory)

        manager = CameraManager(backend=cast("StreamingCameraBackend", FakeBackend()))

        await manager.cleanup()

        mock_clear_directory.assert_awaited_once()
        call_args = mock_clear_directory.call_args
        assert call_args[1]["time_to_live_s"] == settings.image_ttl_s


class TestCameraManagerStartStreaming:
    """Tests for CameraManager.start_streaming method."""

    async def test_raises_when_stream_already_active(self) -> None:
        """Should raise ActiveStreamError if a stream is already active."""
        manager = CameraManager(backend=cast("StreamingCameraBackend", FakeBackend()))
        manager.stream.mode = StreamMode.YOUTUBE
        manager.stream.started_at = datetime.now(UTC)

        with pytest.raises(ActiveStreamError):
            await manager.start_streaming(StreamMode.YOUTUBE)

    async def test_happy_path_youtube_streaming(self) -> None:
        """Should start a stream and expose provider-neutral stream state."""
        backend = FakeBackend()
        backend.start_stream.return_value = StreamStartResult(
            mode=StreamMode.YOUTUBE,
            url=AnyUrl(f"{YOUTUBE_WATCH_URL_PREFIX}valid-broadcast"),
        )
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))

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
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))

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
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))

        with pytest.raises(YoutubeConfigRequiredError):
            await manager.start_streaming(StreamMode.YOUTUBE, youtube_config=None)


class TestCameraManagerCapture:
    """Tests for CameraManager capture flows."""

    async def test_capture_uses_backend_result(self, tmp_path: Path) -> None:
        """Should build an image response from the backend capture result."""
        from pydantic import AnyUrl  # noqa: PLC0415

        from app.api.services.image_sinks.base import StoredImage  # noqa: PLC0415

        backend = FakeBackend()
        image = Image.new("RGB", (64, 64), color="green")
        backend.capture_image.return_value = CaptureResult(
            image=image,
            camera_properties={"Model": "mock-camera"},
            capture_metadata={"FrameDuration": 33_333},
        )

        # Stub sink that reports a successful upload without touching any real backend.
        stub_image_id = "a" * 32  # 32-char hex, matches ImageCaptureResponse.image_id pattern

        class _StubSink:
            put = AsyncMock(
                return_value=StoredImage(
                    image_id=stub_image_id,
                    image_url=AnyUrl(EXAMPLE_IMAGE_URL),
                )
            )

        manager = CameraManager(
            backend=cast("StreamingCameraBackend", backend),
            sink=_StubSink(),
        )
        original = settings.image_path
        settings.image_path = tmp_path / "images"
        settings.image_path.mkdir()

        try:
            response = await manager.capture_jpeg()
        finally:
            settings.image_path = original

        assert response.metadata.camera_properties.camera_model == MOCK_CAMERA
        assert response.image_id == stub_image_id
        backend.capture_image.assert_awaited_once()


class TestCameraManagerStopStreaming:
    """Tests for CameraManager.stop_streaming method."""

    async def test_raises_when_no_stream_active(self) -> None:
        """Should raise RuntimeError if no stream is active."""
        manager = CameraManager(backend=cast("StreamingCameraBackend", FakeBackend()))

        with pytest.raises(RuntimeError, match="No stream active"):
            await manager.stop_streaming()

    async def test_stops_active_stream(self) -> None:
        """Should stop the stream through the backend."""
        backend = FakeBackend()
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))
        manager.stream.mode = StreamMode.YOUTUBE
        manager.stream.url = AnyUrl(f"{YOUTUBE_WATCH_URL_PREFIX}valid-broadcast")
        manager.stream.started_at = datetime.now(UTC)

        await manager.stop_streaming()

        backend.stop_stream.assert_awaited_once()
        assert not manager.stream.is_active


class TestCameraManagerGetStatus:
    """Tests for CameraManager.get_status method."""

    async def test_returns_status_without_stream(self) -> None:
        """Should return status with no stream info when stream is inactive."""
        manager = CameraManager(backend=cast("StreamingCameraBackend", FakeBackend()))

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
        backend.get_controls = AsyncMock(return_value=CameraControlsView(supported=True))
        backend.get_controls_capabilities = AsyncMock(return_value=CameraControlsCapabilities(supported=True))
        backend.set_controls = AsyncMock()
        backend.set_focus = AsyncMock()
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))

        result = await manager.get_controls()

        assert result.supported is True
        backend.get_controls.assert_awaited_once()

    async def test_set_controls_dispatches_to_backend(self) -> None:
        """Should apply generic controls through a controllable backend."""
        backend = FakeBackend()
        backend.get_controls = AsyncMock()
        backend.get_controls_capabilities = AsyncMock(return_value=CameraControlsCapabilities(supported=True))
        backend.set_controls = AsyncMock(return_value=CameraControlsView(supported=True))
        backend.set_focus = AsyncMock()
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))
        patch = CameraControlsPatch(controls={"ExposureTime": 10000})

        result = await manager.set_controls(patch)

        assert result.supported is True
        backend.set_controls.assert_awaited_once_with({"ExposureTime": 10000})

    async def test_set_focus_dispatches_to_backend(self) -> None:
        """Should apply friendly focus controls through a controllable backend."""
        backend = FakeBackend()
        backend.get_controls = AsyncMock()
        backend.get_controls_capabilities = AsyncMock(return_value=CameraControlsCapabilities(supported=True))
        backend.set_controls = AsyncMock()
        backend.set_focus = AsyncMock(return_value=CameraControlsView(supported=True))
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))
        request = FocusControlRequest(mode=FocusMode.CONTINUOUS)

        result = await manager.set_focus(request)

        assert result.supported is True
        backend.set_focus.assert_awaited_once_with(request)

    async def test_get_controls_capabilities_dispatches_to_backend(self) -> None:
        """Should return UI-friendly capabilities from a controllable backend."""
        backend = FakeBackend()
        backend.get_controls = AsyncMock()
        backend.set_controls = AsyncMock()
        backend.set_focus = AsyncMock()
        backend.get_controls_capabilities = AsyncMock(return_value=CameraControlsCapabilities(supported=True))
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))

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
                url=AnyUrl(f"{YOUTUBE_WATCH_URL_PREFIX}valid-broadcast"),
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
                url=AnyUrl(f"{YOUTUBE_WATCH_URL_PREFIX}valid-broadcast"),
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
                url=AnyUrl(f"{YOUTUBE_WATCH_URL_PREFIX}valid-broadcast"),
            )
        )

        view = service.build_view({"Model": MOCK_CAMERA}, {"FrameDuration": 33_333})

        assert view is not None
        assert view.mode == StreamMode.YOUTUBE
