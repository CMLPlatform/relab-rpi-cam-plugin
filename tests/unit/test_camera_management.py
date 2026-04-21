"""Tests for camera management dependencies and orchestration."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from PIL import Image
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.camera import CameraMode
from relab_rpi_cam_models.images import ImageCaptureStatus
from relab_rpi_cam_models.stream import StreamMode

from app.api.dependencies import camera_management as camera_deps
from app.api.exceptions import ActiveStreamError
from app.api.schemas.camera_controls import CameraControlsPatch, CameraControlsView, FocusControlRequest, FocusMode
from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.camera_backend import CaptureResult, StreamingCameraBackend, StreamStartResult
from app.api.services.camera_manager import CameraControlsNotSupportedError, CameraManager
from app.api.services.image_sinks.base import StoredImage
from app.api.services.stream_service import StreamService
from app.core.config import settings
from app.core.runtime import AppRuntime
from tests.constants import EXAMPLE_IMAGE_URL, YOUTUBE_WATCH_URL_PREFIX

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from relab_rpi_cam_models.stream import StreamView

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
        self.set_controls: Callable[[dict[str, Any]], Awaitable[CameraControlsView]] | None = None
        self.set_focus: Callable[[FocusControlRequest], Awaitable[CameraControlsView]] | None = None

    @property
    def camera(self) -> None:
        """Return None for fake backend (no hardware camera)."""
        return None

    async def _open(self, mode: CameraMode) -> None:
        self.current_mode = mode


@dataclass
class ProbeStreamState:
    """Small mutable stream-state stub for dependency tests."""

    is_active: bool = False
    started_at: datetime | None = None
    mode: object | None = None


class ProbeCameraManager(CameraManager):
    """Typed camera-manager probe for dependency helpers."""

    def __init__(self) -> None:
        super().__init__(backend=cast("StreamingCameraBackend", FakeBackend()))
        self.stream_probe = ProbeStreamState()
        self.stop_streaming_mock = AsyncMock()
        self.get_stream_info_mock = AsyncMock(return_value=None)

    @property
    def stream(self) -> ProbeStreamState:
        """Return mutable stream state for tests."""
        return self.stream_probe

    async def stop_streaming(self) -> None:
        """Delegate to the stop-stream mock."""
        await self.stop_streaming_mock()

    async def get_stream_info(self) -> "StreamView | None":
        """Delegate to the get-stream-info mock."""
        return await self.get_stream_info_mock()


@pytest.fixture
def mock_camera_manager() -> ProbeCameraManager:
    """Return a typed camera-manager probe with async mocks."""
    return ProbeCameraManager()


class TestCheckStreamDuration:
    """Tests for check_stream_duration."""

    async def test_stops_overdue_stream(
        self,
        mock_camera_manager: ProbeCameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should stop the stream if it has been active longer than the configured maximum duration."""
        monkeypatch.setattr(settings, "max_stream_duration_s", 10)
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.stream.started_at = datetime.now(UTC) - timedelta(seconds=20)

        await camera_deps.check_stream_duration(mock_camera_manager)

        mock_camera_manager.stop_streaming_mock.assert_awaited_once()


class TestGetCameraManager:
    """Tests for runtime-aware camera manager resolution."""

    def test_prefers_request_runtime_camera_manager(self) -> None:
        """Request-scoped runtime should override the legacy compatibility manager."""
        app = FastAPI()
        runtime = AppRuntime(camera_manager=cast("CameraManager", SimpleNamespace(name="runtime-manager")))
        app.state.runtime = runtime
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/camera",
                "headers": [],
                "query_string": b"",
                "client": ("127.0.0.1", 1234),
                "server": ("testserver", 80),
                "scheme": "http",
                "app": app,
            },
        )

        assert camera_deps.get_camera_manager(request) is runtime.camera_manager

    async def test_ignores_recent_stream(
        self,
        mock_camera_manager: ProbeCameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should not stop the stream if it has been active for less than the configured maximum duration."""
        monkeypatch.setattr(settings, "max_stream_duration_s", 10)
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.stream.started_at = datetime.now(UTC)

        await camera_deps.check_stream_duration(mock_camera_manager)

        mock_camera_manager.stop_streaming_mock.assert_not_awaited()

    async def test_logs_runtime_error(
        self,
        mock_camera_manager: ProbeCameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should log a RuntimeError if stopping the stream fails, but not raise it."""
        monkeypatch.setattr(settings, "max_stream_duration_s", 10)
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.stream.started_at = datetime.now(UTC) - timedelta(seconds=20)
        mock_camera_manager.stop_streaming_mock.side_effect = RuntimeError("boom")

        await camera_deps.check_stream_duration(mock_camera_manager)

        mock_camera_manager.stop_streaming_mock.assert_awaited_once()


class TestCheckStreamHealth:
    """Tests for check_stream_health — the liveness probe for the active stream."""

    async def test_noop_when_no_stream_active(
        self,
        mock_camera_manager: ProbeCameraManager,
    ) -> None:
        """No active stream: the probe returns immediately without touching anything."""
        mock_camera_manager.stream.is_active = False
        mock_camera_manager.get_stream_info_mock = AsyncMock()

        await camera_deps.check_stream_health(mock_camera_manager)

        mock_camera_manager.get_stream_info_mock.assert_not_awaited()
        mock_camera_manager.stop_streaming_mock.assert_not_awaited()

    async def test_stops_stream_when_get_info_returns_none(
        self,
        mock_camera_manager: ProbeCameraManager,
    ) -> None:
        """Get-info reports no info (e.g. ffmpeg died) -> stop the stream for recovery."""
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.get_stream_info_mock = AsyncMock(return_value=None)

        await camera_deps.check_stream_health(mock_camera_manager)

        mock_camera_manager.get_stream_info_mock.assert_awaited_once()
        mock_camera_manager.stop_streaming_mock.assert_awaited_once()

    async def test_tolerates_os_error_and_stops_stream(
        self,
        mock_camera_manager: ProbeCameraManager,
    ) -> None:
        """OSError from get_stream_info should trigger a recovery stop."""
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.get_stream_info_mock = AsyncMock(side_effect=OSError("pipe closed"))

        await camera_deps.check_stream_health(mock_camera_manager)

        mock_camera_manager.stop_streaming_mock.assert_awaited_once()

    async def test_tolerates_runtime_error_and_stops_stream(
        self,
        mock_camera_manager: ProbeCameraManager,
    ) -> None:
        """RuntimeError from get_stream_info should also trigger a recovery stop."""
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.get_stream_info_mock = AsyncMock(side_effect=RuntimeError("crashed"))

        await camera_deps.check_stream_health(mock_camera_manager)

        mock_camera_manager.stop_streaming_mock.assert_awaited_once()

    async def test_suppresses_error_from_recovery_stop(
        self,
        mock_camera_manager: ProbeCameraManager,
    ) -> None:
        """If the recovery stop itself raises RuntimeError, the probe still returns cleanly."""
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.get_stream_info_mock = AsyncMock(side_effect=RuntimeError("crashed"))
        mock_camera_manager.stop_streaming_mock.side_effect = RuntimeError("cannot stop")

        # Should not raise — contextlib.suppress handles it.
        await camera_deps.check_stream_health(mock_camera_manager)

        mock_camera_manager.stop_streaming_mock.assert_awaited_once()

    async def test_healthy_stream_leaves_state_alone(
        self,
        mock_camera_manager: ProbeCameraManager,
    ) -> None:
        """Healthy get_stream_info result: do not stop the stream."""
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.get_stream_info_mock = AsyncMock(return_value=SimpleNamespace(healthy=True))

        await camera_deps.check_stream_health(mock_camera_manager)

        mock_camera_manager.stop_streaming_mock.assert_not_awaited()


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
        status = await manager.get_status()
        assert str(status.last_image_url) == EXAMPLE_IMAGE_URL

    async def test_capture_allows_active_youtube_stream(self, tmp_path: Path) -> None:
        """Normal still capture should remain allowed while a YouTube stream is active."""
        backend = FakeBackend()
        image = Image.new("RGB", (64, 64), color="purple")
        backend.capture_image.return_value = CaptureResult(
            image=image,
            camera_properties={"Model": "mock-camera"},
            capture_metadata={"FrameDuration": 33_333},
        )

        class _StubSink:
            put = AsyncMock(
                return_value=StoredImage(
                    image_id="b" * 32,
                    image_url=AnyUrl(EXAMPLE_IMAGE_URL),
                )
            )

        manager = CameraManager(
            backend=cast("StreamingCameraBackend", backend),
            sink=_StubSink(),
        )
        manager.stream.mode = StreamMode.YOUTUBE
        manager.stream.url = AnyUrl(f"{YOUTUBE_WATCH_URL_PREFIX}active-broadcast")
        manager.stream.started_at = datetime.now(UTC)

        original = settings.image_path
        settings.image_path = tmp_path / "images"
        settings.image_path.mkdir()

        try:
            response = await manager.capture_jpeg()
        finally:
            settings.image_path = original

        assert response.status == ImageCaptureStatus.UPLOADED
        backend.capture_image.assert_awaited_once()

    async def test_capture_allows_video_mode_preview_state(self, tmp_path: Path) -> None:
        """Still capture should keep working while the backend is already in video mode."""
        backend = FakeBackend()
        backend.current_mode = CameraMode.VIDEO
        image = Image.new("RGB", (64, 64), color="orange")
        backend.capture_image.return_value = CaptureResult(
            image=image,
            camera_properties={"Model": "mock-camera"},
            capture_metadata={"FrameDuration": 33_333},
        )

        class _StubSink:
            put = AsyncMock(
                return_value=StoredImage(
                    image_id="c" * 32,
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

        assert response.status == ImageCaptureStatus.UPLOADED
        backend.capture_image.assert_awaited_once()

    async def test_capture_jpeg_writes_atomically(self, tmp_path: Path) -> None:
        """capture_jpeg must not leave a partially-written JPEG if encoding fails.

        Regression guard: previously the encode-then-read-bytes sequence ran outside
        the camera lock and used a non-atomic ``image.save`` call, so disk-full or a
        racing reader could observe a truncated JPEG. The atomic tmp-rename path must
        leave neither the ``.tmp`` nor the final file behind on failure.
        """
        backend = FakeBackend()
        image = Image.new("RGB", (64, 64), color="blue")
        disk_full_msg = "simulated disk full"

        backend.capture_image.return_value = CaptureResult(
            image=image,
            camera_properties={"Model": "mock-camera"},
            capture_metadata={"FrameDuration": 33_333},
        )

        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))
        original = settings.image_path
        settings.image_path = tmp_path / "images"
        settings.image_path.mkdir()

        def _raise_disk_full(*_args: object, **_kwargs: object) -> bytes:
            raise OSError(disk_full_msg)

        try:
            with pytest.MonkeyPatch.context() as monkeypatch:
                monkeypatch.setattr(
                    "app.api.services.camera_manager._encode_jpeg_atomic",
                    _raise_disk_full,
                )
                with pytest.raises(OSError, match=disk_full_msg):
                    await manager.capture_jpeg()
        finally:
            settings.image_path = original

        # Neither the target nor the .tmp sidecar should exist.
        leftover = list((tmp_path / "images").iterdir())
        assert leftover == [], f"atomic encode leaked files: {leftover}"


class TestCameraManagerLocking:
    """Tests for camera-manager lock timeout behavior."""

    async def test_locked_times_out_only_while_waiting_for_lock(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The lock timeout should apply to acquisition, not the whole critical section."""
        manager = CameraManager(backend=cast("StreamingCameraBackend", FakeBackend()))
        monkeypatch.setattr(manager, "lock_timeout", 0.01)
        await manager.lock.acquire()

        try:
            with pytest.raises(RuntimeError, match="Failed to acquire camera lock"):
                async with manager._locked():
                    pytest.fail("critical section should never run when acquisition times out")
        finally:
            manager.lock.release()

    async def test_locked_does_not_time_out_after_lock_is_acquired(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Long work inside the lock should not be cancelled by the acquisition timeout."""
        manager = CameraManager(backend=cast("StreamingCameraBackend", FakeBackend()))
        monkeypatch.setattr(manager, "lock_timeout", 0.01)

        async with manager._locked():
            await asyncio.sleep(0.02)

        assert manager.lock.locked() is False


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
        assert status.last_image_url is None


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
        backend.set_controls = AsyncMock()
        backend.set_focus = AsyncMock(return_value=CameraControlsView(supported=True))
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))
        request = FocusControlRequest(mode=FocusMode.CONTINUOUS)

        result = await manager.set_focus(request)

        assert result.supported is True
        backend.set_focus.assert_awaited_once_with(request)


class TestPreviewThumbnailCapture:
    """Tests for best-effort preview-thumbnail capture."""

    async def test_returns_none_when_stream_is_active(self) -> None:
        """Active streaming should skip preview-thumbnail capture entirely."""
        backend = FakeBackend()
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))
        manager.stream.mode = StreamMode.YOUTUBE
        manager.stream.url = AnyUrl(f"{YOUTUBE_WATCH_URL_PREFIX}preview-active")
        manager.stream.started_at = datetime.now(UTC)

        result = await manager.capture_preview_thumbnail_jpeg()

        assert result is None
        backend.capture_image.assert_not_awaited()

    async def test_returns_jpeg_bytes_from_backend_capture(self) -> None:
        """When idle, the manager should derive a JPEG preview thumbnail from the backend image."""
        backend = FakeBackend()
        backend.capture_image = AsyncMock(
            return_value=CaptureResult(
                image=Image.new("RGB", (1280, 720), color="blue"),
                camera_properties={"Model": MOCK_CAMERA},
                capture_metadata={"FrameDuration": 33_333},
            )
        )
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))

        result = await manager.capture_preview_thumbnail_jpeg()

        assert result is not None
        assert result.startswith(b"\xff\xd8")
        backend.capture_image.assert_awaited_once()

    async def test_returns_none_when_camera_lock_is_busy(self) -> None:
        """A busy camera lock should cause the best-effort thumbnail capture to skip cleanly."""
        backend = FakeBackend()
        manager = CameraManager(backend=cast("StreamingCameraBackend", backend))

        await manager.lock.acquire()
        try:
            result = await manager.capture_preview_thumbnail_jpeg(lock_timeout_s=0.01)
        finally:
            manager.lock.release()

        assert result is None
        backend.capture_image.assert_not_awaited()


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
