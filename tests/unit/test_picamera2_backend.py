"""Tests for the Picamera2 backend implementation."""

from unittest.mock import MagicMock

import pytest
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.camera import CameraMode
from relab_rpi_cam_models.stream import StreamMode

from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.picamera2_backend import Picamera2Backend


class TestPicamera2Backend:
    """Tests for the concrete Picamera2 backend."""

    async def test_open_is_idempotent_once_started(self) -> None:
        """Opening again after the pipeline is running should not reconfigure."""
        backend = Picamera2Backend()
        camera = MagicMock()
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.PHOTO

        await backend.open(CameraMode.VIDEO)

        camera.configure.assert_not_called()
        camera.start.assert_not_called()
        assert backend.current_mode == CameraMode.VIDEO

    async def test_capture_image_reads_main_stream(self) -> None:
        """capture_image must pull from the persistent main stream by name."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_properties = {"Model": "mock"}
        camera.capture_metadata.return_value = {"FrameDuration": 33_333}
        camera.capture_image.return_value = MagicMock()
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.PHOTO

        await backend.capture_image()

        camera.capture_image.assert_called_once_with("main")

    async def test_start_stream_requires_config(self) -> None:
        """YouTube streaming should require YouTube config."""
        backend = Picamera2Backend()

        with pytest.raises(YoutubeConfigRequiredError):
            await backend.start_stream(StreamMode.YOUTUBE, youtube_config=None)

    async def test_start_stream_uses_main_encoder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """start_stream should attach an encoder to the persistent main stream by name."""
        backend = Picamera2Backend()
        camera = MagicMock()
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.VIDEO

        monkeypatch.setattr("app.api.services.picamera2_backend.H264Encoder", MagicMock)
        monkeypatch.setattr("app.api.services.picamera2_backend.get_ffmpeg_output", MagicMock(return_value=object()))

        config = YoutubeStreamConfig(stream_key=SecretStr("good"), broadcast_key=SecretStr("public-id"))
        result = await backend.start_stream(StreamMode.YOUTUBE, youtube_config=config)

        assert result.mode == StreamMode.YOUTUBE
        assert result.url == AnyUrl("https://youtube.com/watch?v=public-id")
        camera.start_encoder.assert_called_once()
        assert camera.start_encoder.call_args.kwargs == {"name": "main"}
        assert backend._main_encoder is camera.start_encoder.call_args.args[0]  # noqa: SLF001
        camera.start_recording.assert_not_called()

    async def test_stop_stream_keeps_camera_running(self) -> None:
        """stop_stream must only detach the encoder — the camera pipeline stays up for stills."""
        backend = Picamera2Backend()
        camera = MagicMock()
        encoder = MagicMock()
        backend._camera = camera  # noqa: SLF001
        backend._main_encoder = encoder  # noqa: SLF001

        await backend.stop_stream()

        camera.stop_encoder.assert_called_once_with(encoder)
        assert backend._main_encoder is None  # noqa: SLF001
        camera.stop.assert_not_called()
        camera.start.assert_not_called()

    async def test_cleanup_releases_camera(self) -> None:
        """Cleanup should stop/close the camera and clear the reference."""
        backend = Picamera2Backend()
        camera = MagicMock()
        backend._camera = camera  # noqa: SLF001
        backend.current_mode = CameraMode.PHOTO

        await backend.cleanup()

        camera.stop.assert_called_once()
        camera.close.assert_called_once()
        assert backend._camera is None  # noqa: SLF001
        assert backend.current_mode is None
