"""Picamera2-backed camera implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, NoReturn, cast

from relab_rpi_cam_models.camera import CameraMode
from relab_rpi_cam_models.stream import StreamMode

from app.api.exceptions import CameraInitializationError
from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.camera_backend import CaptureResult, StreamingCameraBackend, StreamStartResult
from app.api.services.hardware_protocols import Picamera2Like
from app.api.services.hardware_stubs import H264EncoderStub, Picamera2Stub
from app.api.services.stream import get_broadcast_url, get_ffmpeg_output
from app.core.config import settings

if TYPE_CHECKING:
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder
else:
    try:
        from picamera2 import Picamera2
        from picamera2.encoders import H264Encoder
    except ImportError:
        Picamera2 = Picamera2Stub
        H264Encoder = H264EncoderStub

logger = logging.getLogger(__name__)

# Main stream: full-resolution buffer used for stills and, when active, the
# YouTube H264 encoder. Lores stream: low-resolution buffer reserved for the
# preview H264 encoder (MediaMTX WHEP, Phase 6) — much cheaper on CPU than
# encoding main, which matters because preview is the dominant use case while
# YouTube streaming is rare.
_MAIN_SIZE = (1920, 1080)
_LORES_SIZE = (640, 480)


class Picamera2Backend(StreamingCameraBackend):
    """Concrete camera backend backed by Picamera2.

    Runs a single persistent video configuration with both a main (1080p) and a
    lores (640x480) stream. Stills are pulled from the running main stream via
    ``capture_image("main")`` — Pi 5's dual-ISP handles still-while-recording,
    so no mode switching or pipeline restart is needed.
    """

    def __init__(self) -> None:
        self._camera: Picamera2Like | None = None
        self.current_mode: CameraMode | None = None

    async def open(self, mode: CameraMode) -> None:
        """Initialise the persistent pipeline on first call; idempotent thereafter."""
        if self._camera is None:
            try:
                self._camera = cast(
                    "Picamera2Like", await asyncio.to_thread(lambda: Picamera2(camera_num=settings.camera_device_num))
                )
            except IndexError as e:
                raise CameraInitializationError(
                    settings.camera_device_num,
                    "Camera device not found. Check that the device number is correct and the camera is connected.",
                ) from e
            except (RuntimeError, OSError) as e:
                raise CameraInitializationError(settings.camera_device_num, str(e)) from e

            camera = self._require_camera()
            config = camera.create_video_configuration(
                main={"size": _MAIN_SIZE},
                lores={"size": _LORES_SIZE},
                raw=None,
            )
            camera.configure(config)
            await asyncio.to_thread(camera.start)

        self.current_mode = mode

    async def capture_image(self) -> CaptureResult:
        """Capture a still from the running main stream."""
        await self.open(CameraMode.PHOTO)
        camera = self._require_camera()
        image = await asyncio.to_thread(camera.capture_image, "main")
        capture_metadata = await asyncio.wait_for(asyncio.to_thread(camera.capture_metadata), timeout=10)
        if capture_metadata is None:
            msg = "Failed to capture image metadata"
            raise RuntimeError(msg)
        return CaptureResult(
            image=image,
            camera_properties=camera.camera_properties,
            capture_metadata=capture_metadata,
        )

    async def start_stream(
        self,
        mode: StreamMode,
        *,
        youtube_config: YoutubeStreamConfig | None = None,
    ) -> StreamStartResult:
        """Start a provider-backed stream on the main H264 encoder.

        Uses ``start_encoder(name="main")`` instead of ``start_recording`` so the
        persistent picamera2 pipeline (set up in ``open()`` during Phase 1)
        stays up — stills-while-streaming keeps working because the main stream
        continues feeding frames to ``capture_image`` simultaneously.
        """
        if mode == StreamMode.YOUTUBE and not youtube_config:
            raise YoutubeConfigRequiredError

        await self.open(CameraMode.VIDEO)
        camera = self._require_camera()

        try:
            stream_output = get_ffmpeg_output(mode, youtube_config)
            await asyncio.wait_for(
                asyncio.to_thread(camera.start_encoder, H264Encoder(), stream_output, name="main"),
                timeout=30.0,
            )
        except TimeoutError as e:
            msg = "Failed to start recording: ffmpeg startup timeout"
            raise RuntimeError(msg) from e
        except (OSError, RuntimeError) as e:
            msg = f"Failed to start recording: {e}"
            raise RuntimeError(msg) from e

        url = get_broadcast_url(youtube_config) if youtube_config else None
        if url is None:
            await asyncio.to_thread(camera.stop_encoder, name="main")
            _raise_missing_stream_url()

        return StreamStartResult(mode=mode, url=url)

    async def stop_stream(self) -> None:
        """Stop the main encoder without touching the rest of the persistent pipeline."""
        camera = self._require_camera()
        await asyncio.to_thread(camera.stop_encoder, name="main")

    async def get_stream_metadata(self) -> tuple[dict, dict]:
        """Return metadata for the active stream."""
        camera = self._require_camera()
        capture_metadata = await asyncio.wait_for(asyncio.to_thread(camera.capture_metadata), timeout=10)
        if capture_metadata is None:
            msg = "Failed to capture image metadata"
            raise RuntimeError(msg)
        return camera.camera_properties, capture_metadata

    async def cleanup(self) -> None:
        """Release Picamera2 resources."""
        if self._camera:
            await asyncio.to_thread(self._camera.stop)
            await asyncio.to_thread(self._camera.close)
            self._camera = None
            self.current_mode = None

    def _require_camera(self) -> Picamera2Like:
        """Return the initialized camera or raise a runtime error."""
        if self._camera is None:
            msg = "Camera backend has not been initialized"
            raise RuntimeError(msg)
        return self._camera


def _raise_missing_stream_url() -> NoReturn:
    """Raise the standard error for backends that fail to expose a stream URL."""
    msg = "Streaming backend did not return a public stream URL"
    raise RuntimeError(msg)
