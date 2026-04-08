"""Tests for camera management dependencies."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr
from relab_rpi_cam_models.stream import StreamMode, YoutubeStreamConfig

from app.api.dependencies import camera_management as camera_deps
from app.api.exceptions import ActiveStreamError, YouTubeValidationError
from app.api.services.camera_manager import CameraManager
from app.core.config import settings


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
        """Should not call cleanup if the stream is active.

        It may be needed for an ongoing stream and we don't want to disrupt it.
        """
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
        """Should stop the stream if it has been active longer than the configured maximum duration.

        This prevents runaway streaming sessions that could consume resources indefinitely.
        """
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

    async def test_cleanup_uses_correct_ttl(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should call clear_directory with image_ttl_s, not hls_ttl_s."""
        mock_clear_directory = AsyncMock()
        monkeypatch.setattr("app.api.services.camera_manager.clear_directory", mock_clear_directory)

        # Create a real CameraManager with mocked stream and clear_directory
        manager = CameraManager()
        # Mock the camera and stream to avoid initialization issues
        manager.camera = None
        manager.stream = SimpleNamespace(is_active=False)

        await manager.cleanup()

        # Verify clear_directory was called with the correct TTL
        mock_clear_directory.assert_awaited_once()
        call_args = mock_clear_directory.call_args
        assert call_args[1]["time_to_live_s"] == settings.image_ttl_s
        assert call_args[1]["time_to_live_s"] != settings.hls_ttl_s


class TestCameraManagerStartStreaming:
    """Tests for CameraManager.start_streaming method."""

    async def test_raises_when_stream_already_active(self) -> None:
        """Should raise ActiveStreamError if a stream is already active."""
        manager = CameraManager()
        manager.stream.mode = StreamMode.YOUTUBE
        manager.stream.started_at = datetime.now(UTC)

        with pytest.raises(ActiveStreamError):
            await manager.start_streaming(StreamMode.YOUTUBE)

    async def test_raises_youtube_validation_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should raise YouTubeValidationError if YouTube stream key is invalid."""
        manager = CameraManager()
        manager.camera = MagicMock()
        monkeypatch.setattr(manager, "setup_camera", AsyncMock(return_value=manager.camera))

        # Mock validate_stream_key to return False
        async def mock_validate(*_args: object, **_kwargs: object) -> bool:
            return False

        monkeypatch.setattr("app.api.services.camera_manager.validate_stream_key", mock_validate)

        youtube_config = YoutubeStreamConfig(
            stream_key=SecretStr("invalid-key"),
            broadcast_key=SecretStr("invalid-broadcast"),
        )

        with pytest.raises(YouTubeValidationError):
            await manager.start_streaming(StreamMode.YOUTUBE, youtube_config=youtube_config)


    async def test_happy_path_youtube_streaming(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should successfully start a YouTube stream with valid config."""
        manager = CameraManager()
        mock_camera = MagicMock()
        mock_camera.camera_properties = {"Model": "test-camera"}
        mock_camera.capture_metadata = MagicMock(return_value={"FrameDuration": 33333})
        manager.camera = mock_camera
        manager.current_mode = None

        # Mock validate_stream_key to return True
        async def mock_validate(*_args: object, **_kwargs: object) -> bool:
            return True

        monkeypatch.setattr("app.api.services.camera_manager.validate_stream_key", mock_validate)
        # Mock H264Encoder to avoid picamera2 dependency
        monkeypatch.setattr("app.api.services.camera_manager.H264Encoder", MagicMock)

        youtube_config = YoutubeStreamConfig(
            stream_key=SecretStr("valid-key"),
            broadcast_key=SecretStr("valid-broadcast"),
        )

        result = await manager.start_streaming(StreamMode.YOUTUBE, youtube_config=youtube_config)

        assert result.mode == StreamMode.YOUTUBE
        assert manager.stream.is_active
        assert manager.stream.youtube_config == youtube_config
        mock_camera.start_recording.assert_called_once()

    async def test_stream_state_update_failure_rolls_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should roll back recording if stream state update fails."""
        manager = CameraManager()
        mock_camera = MagicMock()
        mock_camera.camera_properties = {"Model": "test-camera"}
        mock_camera.capture_metadata = MagicMock(return_value={"FrameDuration": 33333})
        manager.camera = mock_camera

        async def mock_validate(*_args: object, **_kwargs: object) -> bool:
            return True

        monkeypatch.setattr("app.api.services.camera_manager.validate_stream_key", mock_validate)
        monkeypatch.setattr("app.api.services.camera_manager.H264Encoder", MagicMock)

        # Make get_stream_url raise to simulate state update failure
        monkeypatch.setattr(
            "app.api.services.camera_manager.get_stream_url",
            MagicMock(side_effect=ValueError("bad url")),
        )

        youtube_config = YoutubeStreamConfig(
            stream_key=SecretStr("key"),
            broadcast_key=SecretStr("broadcast"),
        )

        with pytest.raises(ValueError, match="bad url"):
            await manager.start_streaming(StreamMode.YOUTUBE, youtube_config=youtube_config)

        # Recording should have been stopped (rolled back)
        mock_camera.stop_recording.assert_called_once()
        assert not manager.stream.is_active

    async def test_recording_failure_raises_runtime_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should raise RuntimeError when camera.start_recording fails."""
        manager = CameraManager()
        mock_camera = MagicMock()
        mock_camera.start_recording = MagicMock(side_effect=OSError("camera disconnected"))
        manager.camera = mock_camera

        async def mock_validate(*_args: object, **_kwargs: object) -> bool:
            return True

        monkeypatch.setattr("app.api.services.camera_manager.validate_stream_key", mock_validate)
        monkeypatch.setattr("app.api.services.camera_manager.H264Encoder", MagicMock)

        youtube_config = YoutubeStreamConfig(
            stream_key=SecretStr("key"),
            broadcast_key=SecretStr("broadcast"),
        )

        with pytest.raises(RuntimeError, match="camera disconnected"):
            await manager.start_streaming(StreamMode.YOUTUBE, youtube_config=youtube_config)

        assert not manager.stream.is_active

    async def test_requires_youtube_config(self) -> None:
        """Should raise YoutubeConfigRequiredError when no config provided for YouTube mode."""
        from relab_rpi_cam_models.stream import YoutubeConfigRequiredError

        manager = CameraManager()

        with pytest.raises(YoutubeConfigRequiredError):
            await manager.start_streaming(StreamMode.YOUTUBE, youtube_config=None)


class TestCameraManagerStopStreaming:
    """Tests for CameraManager.stop_streaming method."""

    async def test_raises_when_no_stream_active(self) -> None:
        """Should raise RuntimeError if no stream is active."""
        manager = CameraManager()
        # stream.is_active is False by default

        with pytest.raises(RuntimeError, match="No stream active"):
            await manager.stop_streaming()


class TestCameraManagerGetStatus:
    """Tests for CameraManager.get_status method."""

    async def test_returns_status_without_stream(self) -> None:
        """Should return status with no stream info when stream is inactive."""
        manager = CameraManager()

        status = await manager.get_status()

        assert status.current_mode is None
        assert status.stream is None
