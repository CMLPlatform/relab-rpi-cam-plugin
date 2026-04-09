"""Tests for the Picamera2 backend implementation."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.camera import CameraMode
from relab_rpi_cam_models.stream import StreamMode

from app.api.exceptions import YouTubeValidationError
from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.picamera2_backend import Picamera2Backend

DIRECT_PREVIEW_MARKER = "source=direct_preview"
FALLBACK_RESIZE_MARKER = "source=fallback_resize"
DIRECT_DURATION_MARKER = "duration_ms=50.00"
FALLBACK_DURATION_MARKER = "duration_ms=20.00"


class TestPicamera2Backend:
    """Tests for the concrete Picamera2 backend."""

    async def test_open_reuses_current_mode(self) -> None:
        """Opening the same mode twice should not reconfigure twice."""
        backend = Picamera2Backend()
        camera = MagicMock()
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.PHOTO

        await backend.open(CameraMode.PHOTO)

        camera.configure.assert_not_called()

    async def test_start_stream_requires_config(self) -> None:
        """YouTube streaming should require YouTube config."""
        backend = Picamera2Backend()

        with pytest.raises(YoutubeConfigRequiredError):
            await backend.start_stream(StreamMode.YOUTUBE, youtube_config=None)

    async def test_start_stream_validates_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid stream keys should raise a validation error."""
        backend = Picamera2Backend()
        camera = MagicMock()
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.VIDEO

        async def _invalid(*_args: object, **_kwargs: object) -> bool:
            return False

        monkeypatch.setattr("app.api.services.picamera2_backend.validate_stream_key", _invalid)
        config = YoutubeStreamConfig(stream_key=SecretStr("bad"), broadcast_key=SecretStr("bad"))

        with pytest.raises(YouTubeValidationError):
            await backend.start_stream(StreamMode.YOUTUBE, youtube_config=config)

    async def test_start_stream_returns_public_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A successful stream start should return provider-neutral stream state."""
        backend = Picamera2Backend()
        camera = MagicMock()
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.VIDEO

        async def _valid(*_args: object, **_kwargs: object) -> bool:
            return True

        monkeypatch.setattr("app.api.services.picamera2_backend.validate_stream_key", _valid)
        monkeypatch.setattr("app.api.services.picamera2_backend.H264Encoder", MagicMock)
        monkeypatch.setattr("app.api.services.picamera2_backend.get_ffmpeg_output", MagicMock(return_value=object()))

        config = YoutubeStreamConfig(stream_key=SecretStr("good"), broadcast_key=SecretStr("public-id"))
        result = await backend.start_stream(StreamMode.YOUTUBE, youtube_config=config)

        assert result.mode == StreamMode.YOUTUBE
        assert result.url == AnyUrl("https://youtube.com/watch?v=public-id")
        camera.start_recording.assert_called_once()

    async def test_preview_direct_path_logs_timing_and_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Direct preview capture should log duration and source."""
        backend = Picamera2Backend()
        camera = MagicMock()
        preview_image = MagicMock()
        preview_image.save = MagicMock()
        camera.switch_mode_and_capture_image.return_value = preview_image
        camera.create_preview_configuration.return_value = {"preview": True}
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.PHOTO
        monkeypatch.setattr("app.api.services.picamera2_backend.time.perf_counter", MagicMock(side_effect=[1.0, 1.05]))

        with caplog.at_level(logging.DEBUG):
            await backend.capture_preview_jpeg()

        assert DIRECT_PREVIEW_MARKER in caplog.text
        assert DIRECT_DURATION_MARKER in caplog.text

    async def test_preview_fallback_logs_timing_and_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Fallback preview capture should log duration and source."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.switch_mode_and_capture_image.side_effect = RuntimeError("no preview")
        camera.create_preview_configuration.return_value = {"preview": True}
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.PHOTO
        fallback_image = MagicMock()
        resized_image = MagicMock()
        resized_image.save = MagicMock()
        fallback_image.resize.return_value = resized_image
        monkeypatch.setattr(backend, "capture_image", AsyncMock(return_value=MagicMock(image=fallback_image)))
        monkeypatch.setattr("app.api.services.picamera2_backend.time.perf_counter", MagicMock(side_effect=[5.0, 5.02]))

        with caplog.at_level(logging.DEBUG):
            await backend.capture_preview_jpeg()

        assert FALLBACK_RESIZE_MARKER in caplog.text
        assert FALLBACK_DURATION_MARKER in caplog.text

    async def test_preview_config_is_cached(self) -> None:
        """Preview config should be created once and reused."""
        backend = Picamera2Backend()
        camera = MagicMock()
        preview_image = MagicMock()
        preview_image.save = MagicMock()
        camera.create_preview_configuration.return_value = {"preview": True}
        camera.switch_mode_and_capture_image.return_value = preview_image
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.PHOTO

        await backend.capture_preview_jpeg()
        await backend.capture_preview_jpeg()

        camera.create_preview_configuration.assert_called_once()
        assert camera.switch_mode_and_capture_image.call_count == 2

    async def test_cleanup_clears_cached_configs(self) -> None:
        """Cleanup should reset cached preview/still/video configs."""
        backend = Picamera2Backend()
        camera = MagicMock()
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.PHOTO
        backend._still_config = {"still": True}  # noqa: SLF001
        backend._video_config = {"video": True}  # noqa: SLF001
        backend._preview_config = {"preview": True}  # noqa: SLF001

        await backend.cleanup()

        assert backend._still_config is None  # noqa: SLF001
        assert backend._video_config is None  # noqa: SLF001
        assert backend._preview_config is None  # noqa: SLF001
