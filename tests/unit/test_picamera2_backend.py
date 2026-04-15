"""Tests for the Picamera2 backend implementation."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.camera import CameraMode
from relab_rpi_cam_models.stream import StreamMode

from app.api.schemas.camera_controls import FocusControlRequest, FocusMode
from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.picamera2_backend import Picamera2Backend

# ``libcamera`` is only installable on Raspberry Pi OS (ships via apt, not pip).
# Skip this module entirely on dev hosts so the rest of the suite can run.
libcamera = pytest.importorskip("libcamera")
controls = libcamera.controls


class TestPicamera2Backend:
    """Tests for the concrete Picamera2 backend."""

    _NAMESPACE = "picamera2"
    _AF_MODE_AUTO = "Auto"
    _AF_STATE_FOCUSED = "Focused"

    async def test_open_is_idempotent_once_started(self) -> None:
        """Opening again after the pipeline is running should not reconfigure."""
        backend = Picamera2Backend()
        camera = MagicMock()
        cast("Any", backend)._camera = camera
        backend.current_mode = CameraMode.PHOTO

        await backend.open(CameraMode.VIDEO)

        camera.configure.assert_not_called()
        camera.start.assert_not_called()
        assert backend.current_mode == CameraMode.VIDEO

    async def test_open_enables_continuous_autofocus_when_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Opening should default to continuous autofocus when AfMode is available."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"AfMode": (0, 2, 1)}
        camera.create_video_configuration.return_value = {"config": "video"}
        monkeypatch.setattr("app.api.services.picamera2_backend.Picamera2", MagicMock(return_value=camera))

        await backend.open(CameraMode.VIDEO)

        camera.set_controls.assert_called_once_with({"AfMode": controls.AfModeEnum.Continuous})

    async def test_open_skips_autofocus_when_not_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Opening should not touch focus controls when AfMode is absent."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"ExposureTime": (1, 1000, 100)}
        camera.create_video_configuration.return_value = {"config": "video"}
        monkeypatch.setattr("app.api.services.picamera2_backend.Picamera2", MagicMock(return_value=camera))

        await backend.open(CameraMode.VIDEO)

        camera.set_controls.assert_not_called()

    async def test_capture_image_reads_main_stream(self) -> None:
        """capture_image must pull from the persistent main stream by name."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_properties = {"Model": "mock"}
        camera.capture_metadata.return_value = {"FrameDuration": 33_333}
        camera.capture_image.return_value = MagicMock()
        cast("Any", backend)._camera = camera
        backend.current_mode = CameraMode.PHOTO

        await backend.capture_image()

        camera.capture_image.assert_called_once_with("main")

    async def test_start_stream_requires_config(self) -> None:
        """YouTube streaming should require YouTube config."""
        backend = Picamera2Backend()

        with pytest.raises(YoutubeConfigRequiredError):
            await backend.start_stream(StreamMode.YOUTUBE, youtube_config=None)

    async def test_start_stream_uses_main_encoder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """start_stream should attach an encoder to the persistent main stream and patch MediaMTX."""
        backend = Picamera2Backend()
        camera = MagicMock()
        cast("Any", backend)._camera = camera
        backend.current_mode = CameraMode.VIDEO
        # Stub the MediaMTX patch calls — the runtime client would otherwise
        # try to PATCH the localhost API which doesn't exist in unit tests.
        mediamtx = cast("Any", backend._mediamtx)
        mediamtx.set_youtube_egress = AsyncMock()
        mediamtx.clear_egress = AsyncMock()

        monkeypatch.setattr("app.api.services.picamera2_backend.H264Encoder", MagicMock)
        monkeypatch.setattr(
            "app.api.services.picamera2_backend.build_hires_rtsp_output",
            MagicMock(return_value=object()),
        )

        config = YoutubeStreamConfig(stream_key=SecretStr("good"), broadcast_key=SecretStr("public-id"))
        result = await backend.start_stream(StreamMode.YOUTUBE, youtube_config=config)

        assert result.mode == StreamMode.YOUTUBE
        assert result.url == AnyUrl("https://youtube.com/watch?v=public-id")
        # The MediaMTX egress is configured BEFORE the encoder publishes — no
        # half-started state on failure.
        mediamtx.set_youtube_egress.assert_awaited_once_with("cam-hires", "good")
        camera.start_encoder.assert_called_once()
        assert camera.start_encoder.call_args.kwargs == {"name": "main"}
        assert backend._main_encoder is camera.start_encoder.call_args.args[0]
        camera.start_recording.assert_not_called()

    async def test_stop_stream_keeps_camera_running(self) -> None:
        """stop_stream must only detach the encoder — the camera pipeline stays up for stills."""
        backend = Picamera2Backend()
        camera = MagicMock()
        encoder = MagicMock()
        mediamtx = cast("Any", backend._mediamtx)
        mediamtx.clear_egress = AsyncMock()
        cast("Any", backend)._camera = camera
        cast("Any", backend)._main_encoder = encoder

        await backend.stop_stream()

        camera.stop_encoder.assert_called_once_with(encoder)
        assert backend._main_encoder is None
        camera.stop.assert_not_called()
        camera.start.assert_not_called()

    async def test_get_controls_returns_camera_controls_and_metadata(self) -> None:
        """get_controls should expose Picamera2 camera_controls and latest metadata."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {
            "AfMode": (controls.AfModeEnum.Manual, controls.AfModeEnum.Continuous, controls.AfModeEnum.Auto),
            "ExposureTime": (1, 1_000_000, 10_000),
        }
        camera.capture_metadata.return_value = {"AfState": controls.AfStateEnum.Focused, "ExposureTime": 10_000}
        cast("Any", backend)._camera = camera
        backend.current_mode = CameraMode.VIDEO

        view = await backend.get_controls()

        assert view.supported is True
        assert view.controls["AfMode"].namespace == self._NAMESPACE
        assert view.controls["AfMode"].options == ["manual", "auto", "continuous"]
        assert view.controls["AfMode"].default == self._AF_MODE_AUTO
        assert view.values["AfState"] == self._AF_STATE_FOCUSED
        assert view.values["ExposureTime"] == 10_000

    async def test_set_controls_rejects_unknown_control(self) -> None:
        """set_controls should reject controls not reported by Picamera2."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"ExposureTime": (1, 1_000_000, 10_000)}
        cast("Any", backend)._camera = camera
        backend.current_mode = CameraMode.VIDEO

        with pytest.raises(ValueError, match="Unknown camera controls: Nope"):
            await backend.set_controls({"Nope": 1})

        camera.set_controls.assert_not_called()

    async def test_set_controls_maps_afmode_string(self) -> None:
        """set_controls should accept friendly AfMode strings for the generic endpoint."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"AfMode": (0, 2, 1)}
        camera.capture_metadata.return_value = {"AfState": controls.AfStateEnum.Focused}
        cast("Any", backend)._camera = camera
        backend.current_mode = CameraMode.VIDEO

        await backend.set_controls({"AfMode": "continuous"})

        camera.set_controls.assert_called_once_with({"AfMode": controls.AfModeEnum.Continuous})

    async def test_set_focus_continuous_maps_to_afmode(self) -> None:
        """set_focus should map continuous mode to the libcamera enum."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"AfMode": (0, 2, 1)}
        camera.capture_metadata.return_value = {}
        cast("Any", backend)._camera = camera
        backend.current_mode = CameraMode.VIDEO

        await backend.set_focus(FocusControlRequest(mode=FocusMode.CONTINUOUS))

        camera.set_controls.assert_called_once_with({"AfMode": controls.AfModeEnum.Continuous})

    async def test_set_focus_auto_cycle_uses_autofocus_cycle(self) -> None:
        """set_focus should run the Picamera2 autofocus cycle for one-shot autofocus."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"AfMode": (0, 2, 1)}
        camera.capture_metadata.return_value = {}
        cast("Any", backend)._camera = camera
        backend.current_mode = CameraMode.VIDEO

        await backend.set_focus(FocusControlRequest(mode=FocusMode.AUTO, trigger_cycle=True))

        camera.autofocus_cycle.assert_called_once_with(wait=True)
        camera.set_controls.assert_not_called()

    async def test_set_focus_manual_sets_lens_position(self) -> None:
        """set_focus should pass manual lens position through to Picamera2."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"AfMode": (0, 2, 1), "LensPosition": (0.0, 10.0, 1.0)}
        camera.capture_metadata.return_value = {}
        backend._camera = camera
        backend.current_mode = CameraMode.VIDEO

        await backend.set_focus(FocusControlRequest(mode=FocusMode.MANUAL, lens_position=2.5))

        camera.set_controls.assert_called_once_with({"AfMode": controls.AfModeEnum.Manual, "LensPosition": 2.5})

    async def test_get_controls_capabilities_returns_sorted_list(self) -> None:
        """get_controls_capabilities should return a sorted list of control info."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {
            "ExposureTime": (1, 1_000_000, 10_000),
            "AfMode": (controls.AfModeEnum.Manual, controls.AfModeEnum.Continuous, controls.AfModeEnum.Auto),
        }
        camera.capture_metadata.return_value = {}
        backend._camera = camera
        backend.current_mode = CameraMode.VIDEO

        caps = await backend.get_controls_capabilities()

        assert caps.supported is True
        assert [control.name for control in caps.controls] == ["AfMode", "ExposureTime"]

    async def test_cleanup_releases_camera(self) -> None:
        """Cleanup should stop/close the camera and clear the reference."""
        backend = Picamera2Backend()
        camera = MagicMock()
        backend._camera = camera
        backend.current_mode = CameraMode.PHOTO

        await backend.cleanup()

        camera.stop.assert_called_once()
        camera.close.assert_called_once()
        assert backend._camera is None
        assert backend.current_mode is None
