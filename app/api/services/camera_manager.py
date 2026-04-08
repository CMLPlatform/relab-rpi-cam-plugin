"""Main camera manager service class."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.parse import urljoin

from pydantic import AnyUrl

try:
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder
except ImportError:
    from app.api.services._stubs import H264EncoderStub as H264Encoder  # type: ignore[assignment]
    from app.api.services._stubs import Picamera2Stub as Picamera2  # type: ignore[assignment]


from relab_rpi_cam_models.camera import CameraMode, CameraStatusView
from relab_rpi_cam_models.images import ImageCaptureResponse, ImageMetadata
from relab_rpi_cam_models.stream import (
    Stream,
    StreamMode,
    StreamView,
    YoutubeConfigRequiredError,
    YoutubeStreamConfig,
)

from app.api.services.stream import get_ffmpeg_output, get_stream_url, validate_stream_key
from app.core.config import settings
from app.utils.files import clear_directory


class YouTubeValidationError(Exception):
    """Raised when YouTube stream key validation fails."""

    def __init__(self, stream_key: str | None = None) -> None:
        super().__init__(f"Invalid YouTube stream key{f': {stream_key}' if stream_key else ''}.")


class ActiveStreamError(Exception):
    """Raised when trying to access the camera while a stream is active."""

    def __init__(self, stream: Stream) -> None:
        self.mode = stream.mode
        self.url = stream.url
        super().__init__(f"Stream active in {self.mode} mode at {self.url}. Stop streaming first.")


class CameraManager:
    """Main camera manager class which handles camera setup, streaming, and cleanup."""

    def __init__(self) -> None:
        self.camera: Picamera2 | None = None
        self.current_mode: CameraMode | None = None
        self.stream = Stream()
        self.lock = asyncio.Lock()
        self.lock_timeout = 10

    @asynccontextmanager
    async def _camera_lock(self) -> AsyncGenerator[None]:
        """Context manager for camera lock with timeout."""
        try:
            await asyncio.wait_for(self.lock.acquire(), timeout=self.lock_timeout)
            yield
        except TimeoutError as e:
            err_msg = f"Failed to acquire camera lock - timeout error: {e}"
            raise RuntimeError(err_msg) from e
        finally:
            if self.lock.locked():
                self.lock.release()

    @staticmethod
    def _get_camera_config(mode: CameraMode, camera: Picamera2) -> dict:
        """Camera configuration generator."""
        match mode:
            case CameraMode.PHOTO:
                return camera.create_still_configuration(main={"size": (1920, 1080)}, raw=None)
            case CameraMode.VIDEO:
                return camera.create_video_configuration(raw=None)

    async def setup_camera(self, mode: CameraMode) -> Picamera2:
        """Setup camera for specific mode."""
        if self.stream.is_active and mode == CameraMode.PHOTO:
            raise ActiveStreamError(self.stream)

        async with self._camera_lock():
            if self.camera is None:
                # Create camera instance if it doesn't exist
                self.camera = await asyncio.to_thread(lambda: Picamera2(camera_num=settings.camera_device_num))
            elif self.current_mode == mode:
                # Camera already set up for this mode
                return self.camera
            else:
                # Stop camera if it's running before switching modes
                await asyncio.to_thread(self.camera.stop)

            config = self._get_camera_config(mode, self.camera)  # pyright: ignore reportOptionalMemberAccess  # Camera is guaranteed to be initialized by the above lines
            self.camera.configure(config)  # pyright: ignore reportOptionalMemberAccess
            await asyncio.to_thread(self.camera.start)  # pyright: ignore reportOptionalMemberAccess

            self.current_mode = mode
            return self.camera

    async def capture_jpeg(self) -> ImageCaptureResponse:
        """Capture image and return JPEG bytes."""
        camera = await self.setup_camera(CameraMode.PHOTO)
        async with self._camera_lock():
            # Capture image
            pil_image = await asyncio.to_thread(camera.capture_image)

            # Capture metadata
            if (capture_metadata := await asyncio.to_thread(camera.capture_metadata)) is None:
                err_msg = "Failed to capture image metadata"
                raise RuntimeError(err_msg)
            img_metadata = ImageMetadata.from_metadata(pil_image, camera.camera_properties, capture_metadata)

            # Save image to local file
            image_id = uuid.uuid4().hex
            image_path = settings.image_path / f"{image_id}.jpg"
            await asyncio.to_thread(pil_image.save, image_path, exif=img_metadata.to_exif(), format="JPEG", quality=90)

            expires_at = datetime.now(UTC) + timedelta(seconds=settings.image_ttl_s)

        return ImageCaptureResponse(
            image_id=image_id,
            image_url=AnyUrl(urljoin(str(settings.base_url), f"/images/{image_id}")),
            metadata=img_metadata,
            expires_at=expires_at,
        )

    async def capture_preview_jpeg(self) -> bytes:
        """Capture a low-res JPEG for viewfinder preview. Does not save to disk."""
        camera = await self.setup_camera(CameraMode.PHOTO)
        async with self._camera_lock():
            pil_image = await asyncio.to_thread(camera.capture_image)
        pil_image = pil_image.resize((640, 480))
        buf = BytesIO()
        pil_image.save(buf, format="JPEG", quality=70)
        return buf.getvalue()

    async def start_streaming(
        self,
        mode: StreamMode,
        *,
        youtube_config: YoutubeStreamConfig | None = None,
    ) -> StreamView:
        """Start streaming to YouTube or local file."""
        if mode == StreamMode.YOUTUBE:
            if not youtube_config:
                raise YoutubeConfigRequiredError
            if not await validate_stream_key(youtube_config):
                raise YouTubeValidationError(youtube_config.stream_key)

        if self.stream.is_active:
            raise ActiveStreamError(self.stream)

        camera = await self.setup_camera(CameraMode.VIDEO)

        async with self._camera_lock():
            try:
                stream_output = get_ffmpeg_output(mode, youtube_config)
                await asyncio.to_thread(camera.start_recording, H264Encoder(), stream_output)
            except (OSError, RuntimeError) as e:
                err_msg = f"Failed to start recording: {e}"
                raise RuntimeError(err_msg) from e

            try:
                self.stream.mode = mode
                self.stream.url = get_stream_url(mode, youtube_config)
                self.stream.started_at = datetime.now(UTC) - timedelta(seconds=5)
                self.stream.youtube_config = youtube_config
            except Exception:
                # Roll back: stop the recording we just started so it doesn't leak
                await asyncio.to_thread(camera.stop_recording)
                self.stream = Stream()
                raise

        if (stream_info := await self.get_stream_info()) is None:
            err_msg = "Failed to get stream information"
            raise RuntimeError(err_msg)

        return stream_info

    async def stop_streaming(self) -> None:
        """Stop streaming to YouTube or local file."""
        async with self._camera_lock():
            if self.stream.is_active and self.camera:
                await asyncio.to_thread(self.camera.stop_recording)
                if self.stream.mode == StreamMode.LOCAL:
                    await clear_directory(settings.hls_path, time_to_live_s=settings.hls_ttl_s)
                self.stream = Stream()  # Reset stream state
            else:
                err_msg = "No stream active"
                raise RuntimeError(err_msg)

    async def cleanup(self, *, force: bool = False) -> None:
        """Clean up camera and streaming resources. If force is True, this happens even if there is an active stream."""
        if self.stream.is_active and not force:
            raise ActiveStreamError(self.stream)

        if self.stream.is_active:
            await self.stop_streaming()

        await clear_directory(settings.image_path, time_to_live_s=settings.hls_ttl_s)

        async with self._camera_lock():
            if self.camera:
                await asyncio.to_thread(self.camera.stop)
                await asyncio.to_thread(self.camera.close)
                self.camera = None
                self.current_mode = None

    async def get_stream_info(self) -> StreamView | None:
        """Get stream information including metadata if active."""
        if self.camera and self.stream.is_active:
            if (capture_metadata := await asyncio.to_thread(self.camera.capture_metadata)) is None:
                err_msg = "Failed to capture image metadata"
                raise RuntimeError(err_msg)
            return self.stream.get_info(
                camera_properties=self.camera.camera_properties,
                capture_metadata=capture_metadata,
            )
        # Return empty stream view if no stream is active
        return None

    async def get_status(self) -> CameraStatusView:
        """Return the current camera status including active stream info."""
        stream_info = await self.get_stream_info()
        return CameraStatusView(current_mode=self.current_mode, stream=stream_info if self.stream.is_active else None)
