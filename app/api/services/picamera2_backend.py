"""Picamera2-backed camera implementation."""

from __future__ import annotations

import asyncio
import logging
import time
from io import BytesIO
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from relab_rpi_cam_models.camera import CameraMode
from relab_rpi_cam_models.stream import StreamMode

from app.api.exceptions import CameraInitializationError, YouTubeValidationError
from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.camera_backend import CameraBackend, CaptureResult, StreamStartResult
from app.api.services.hardware_protocols import Picamera2Like
from app.api.services.hardware_stubs import H264EncoderStub, Picamera2Stub
from app.api.services.stream import get_broadcast_url, get_ffmpeg_output, validate_stream_key
from app.core.config import settings

if TYPE_CHECKING:
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder
    from PIL.Image import Image as PilImage
else:
    PilImage = object
    try:
        from picamera2 import Picamera2
        from picamera2.encoders import H264Encoder
    except ImportError:
        Picamera2 = Picamera2Stub
        H264Encoder = H264EncoderStub

logger = logging.getLogger(__name__)
_PREVIEW_SIZE = (640, 480)


class Picamera2Backend(CameraBackend):
    """Concrete camera backend backed by Picamera2."""

    def __init__(self) -> None:
        self._camera: Picamera2Like | None = None
        self.current_mode: CameraMode | None = None
        self._still_config: dict | None = None
        self._video_config: dict | None = None
        self._preview_config: dict | None = None

    def _get_camera_config(self, mode: CameraMode, camera: Picamera2Like) -> dict:
        """Build or reuse the underlying Picamera2 config for the requested mode."""
        match mode:
            case CameraMode.PHOTO:
                if self._still_config is None:
                    self._still_config = camera.create_still_configuration(main={"size": (1920, 1080)}, raw=None)
                return self._still_config
            case CameraMode.VIDEO:
                if self._video_config is None:
                    self._video_config = camera.create_video_configuration(raw=None)
                return self._video_config
            case _:
                msg = f"Unhandled camera mode: {mode}"
                raise ValueError(msg)

    def _get_preview_config(self, camera: Picamera2Like) -> dict:
        """Build or reuse the low-resolution preview configuration."""
        if self._preview_config is None:
            self._preview_config = camera.create_preview_configuration(main={"size": _PREVIEW_SIZE}, raw=None)
        return self._preview_config

    async def open(self, mode: CameraMode) -> None:
        """Initialize or reconfigure the Picamera2 camera."""
        if self._camera is None:
            try:
                self._camera = cast(Picamera2Like, await asyncio.to_thread(lambda: Picamera2(camera_num=settings.camera_device_num)))
            except IndexError as e:
                raise CameraInitializationError(
                    settings.camera_device_num,
                    "Camera device not found. Check that the device number is correct and the camera is connected.",
                ) from e
            except (RuntimeError, OSError) as e:
                raise CameraInitializationError(settings.camera_device_num, str(e)) from e
        elif self.current_mode == mode:
            return
        else:
            await asyncio.to_thread(self._camera.stop)

        camera = self._require_camera()
        config = self._get_camera_config(mode, camera)
        camera.configure(config)
        await asyncio.to_thread(camera.start)
        self.current_mode = mode

    async def capture_image(self) -> CaptureResult:
        """Capture a full-resolution image and metadata."""
        await self.open(CameraMode.PHOTO)
        camera = self._require_camera()
        image = await asyncio.to_thread(camera.capture_image)
        capture_metadata = await asyncio.wait_for(asyncio.to_thread(camera.capture_metadata), timeout=10)
        if capture_metadata is None:
            msg = "Failed to capture image metadata"
            raise RuntimeError(msg)
        return CaptureResult(
            image=image,
            camera_properties=camera.camera_properties,
            capture_metadata=capture_metadata,
        )

    async def capture_preview_jpeg(self) -> bytes:
        """Capture a low-resolution preview JPEG."""
        started = time.perf_counter()
        preview, source = await self._capture_preview_image()
        buf = BytesIO()
        preview.save(buf, format="JPEG", quality=70)
        duration_ms = (time.perf_counter() - started) * 1000
        logger.debug("Preview capture source=%s duration_ms=%.2f", source, duration_ms)
        return buf.getvalue()

    async def start_stream(
        self,
        mode: StreamMode,
        *,
        youtube_config: YoutubeStreamConfig | None = None,
    ) -> StreamStartResult:
        """Start a provider-backed stream."""
        if mode == StreamMode.YOUTUBE:
            if not youtube_config:
                raise YoutubeConfigRequiredError
            if not await validate_stream_key(youtube_config):
                raise YouTubeValidationError

        await self.open(CameraMode.VIDEO)
        camera = self._require_camera()

        try:
            stream_output = get_ffmpeg_output(mode, youtube_config)
            await asyncio.wait_for(
                asyncio.to_thread(camera.start_recording, H264Encoder(), stream_output),
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
            await asyncio.to_thread(camera.stop_recording)
            _raise_missing_stream_url()

        return StreamStartResult(mode=mode, url=url)

    async def stop_stream(self) -> None:
        """Stop the current recording session."""
        camera = self._require_camera()
        await asyncio.to_thread(camera.stop_recording)

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
            self._still_config = None
            self._video_config = None
            self._preview_config = None

    def _require_camera(self) -> Picamera2Like:
        """Return the initialized camera or raise a runtime error."""
        if self._camera is None:
            msg = "Camera backend has not been initialized"
            raise RuntimeError(msg)
        return self._camera

    async def _capture_preview_image(self) -> tuple[PilImage, Literal["direct_preview", "fallback_resize"]]:
        """Try a dedicated low-res preview capture, then fall back to resize."""
        await self.open(CameraMode.PHOTO)
        camera = self._require_camera()
        try:
            image = await asyncio.to_thread(camera.switch_mode_and_capture_image, self._get_preview_config(camera))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug("Falling back to resized still capture for preview", exc_info=True)
        else:
            self.current_mode = CameraMode.PHOTO
            return image, "direct_preview"

        result = await self.capture_image()
        return result.image.resize(_PREVIEW_SIZE), "fallback_resize"


def _raise_missing_stream_url() -> NoReturn:
    """Raise the standard error for backends that fail to expose a stream URL."""
    msg = "Streaming backend did not return a public stream URL"
    raise RuntimeError(msg)
