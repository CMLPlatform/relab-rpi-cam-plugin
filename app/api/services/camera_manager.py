"""Main camera manager service class."""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.parse import urljoin

from pydantic import AnyUrl
from relab_rpi_cam_models.camera import CameraMode, CameraStatusView
from relab_rpi_cam_models.images import ImageCaptureResponse
from relab_rpi_cam_models.stream import StreamMode, StreamView

from app.api.exceptions import ActiveStreamError
from app.api.schemas.streaming import YoutubeStreamConfig
from app.api.services.backend_factory import create_camera_backend
from app.api.services.camera_backend import CameraBackend, StreamingCameraBackend
from app.api.services.contract_adapters import build_image_metadata, image_metadata_to_exif
from app.api.services.stream_service import StreamService
from app.api.services.stream_state import ActiveStreamState
from app.core.config import settings
from app.utils.files import clear_directory

logger = logging.getLogger(__name__)
_PREVIEW_SIZE = (640, 480)


class StreamingNotSupportedError(RuntimeError):
    """Raised when a streaming operation is attempted on a non-streaming backend."""

    def __init__(self, backend: CameraBackend) -> None:
        super().__init__(f"Backend {type(backend).__name__} does not support live streaming")


class CameraManager:
    """Main camera manager class which handles camera setup, streaming, and cleanup."""

    def __init__(self, backend: CameraBackend | None = None) -> None:
        self.backend = backend or create_camera_backend()
        self.stream_service = StreamService()
        self.lock = asyncio.Lock()
        self.lock_timeout = 10

    @property
    def stream(self) -> ActiveStreamState:
        """Expose active stream state for existing callers."""
        return self.stream_service.state

    async def _acquire_lock(self) -> None:
        """Acquire the camera lock with a timeout."""
        try:
            await asyncio.wait_for(self.lock.acquire(), timeout=self.lock_timeout)
        except TimeoutError as e:
            err_msg = f"Failed to acquire camera lock - timeout error: {e}"
            raise RuntimeError(err_msg) from e

    async def setup_camera(self, mode: CameraMode) -> None:
        """Prepare the configured backend for the requested camera mode."""
        if self.stream.is_active and mode == CameraMode.PHOTO:
            raise ActiveStreamError(self.stream)

        await self._acquire_lock()
        try:
            await self.backend.open(mode)
        finally:
            self.lock.release()

    async def capture_jpeg(self) -> ImageCaptureResponse:
        """Capture image and return JPEG bytes."""
        if self.stream.is_active:
            raise ActiveStreamError(self.stream)

        await self._acquire_lock()
        try:
            result = await self.backend.capture_image()
            img_metadata = build_image_metadata(result.image, result.camera_properties, result.capture_metadata)

            # Save image to local file
            image_id = uuid.uuid4().hex
            image_path = settings.image_path / f"{image_id}.jpg"
            await asyncio.to_thread(
                result.image.save,
                image_path,
                exif=image_metadata_to_exif(img_metadata),
                format="JPEG",
                quality=90,
            )

            expires_at = datetime.now(UTC) + timedelta(seconds=settings.image_ttl_s)
        finally:
            self.lock.release()

        return ImageCaptureResponse(
            image_id=image_id,
            image_url=AnyUrl(urljoin(str(settings.base_url), f"/images/{image_id}")),
            metadata=img_metadata,
            expires_at=expires_at,
        )

    async def capture_preview_jpeg(self) -> bytes:
        """Capture a low-res JPEG for the polling-preview fallback.

        The dominant preview path in the web UI is MediaMTX WHEP (Phase 6). This
        method exists for native clients and as a diagnostic: it runs a regular
        still capture and resizes. Because the backend pipeline is persistent,
        no mode switch happens and the full path is ~10× faster than it used
        to be.
        """
        if self.stream.is_active:
            raise ActiveStreamError(self.stream)

        started = time.perf_counter()
        await self._acquire_lock()
        try:
            result = await self.backend.capture_image()
        finally:
            self.lock.release()

        preview = result.image.resize(_PREVIEW_SIZE)
        buf = BytesIO()
        preview.save(buf, format="JPEG", quality=70)
        logger.debug("Preview capture duration_ms=%.2f", (time.perf_counter() - started) * 1000)
        return buf.getvalue()

    def _require_streaming_backend(self) -> StreamingCameraBackend:
        """Return the backend narrowed to StreamingCameraBackend, or raise."""
        if not isinstance(self.backend, StreamingCameraBackend):
            raise StreamingNotSupportedError(self.backend)
        return self.backend

    async def start_streaming(
        self,
        mode: StreamMode,
        *,
        youtube_config: YoutubeStreamConfig | None = None,
    ) -> StreamView:
        """Start streaming for the requested provider/mode."""
        backend = self._require_streaming_backend()
        if self.stream.is_active:
            raise ActiveStreamError(self.stream)

        await self._acquire_lock()
        try:
            try:
                result = await backend.start_stream(mode, youtube_config=youtube_config)
                self.stream_service.start(result)
            except Exception:
                self.stream_service.reset()
                raise
        finally:
            self.lock.release()

        if (stream_info := await self.get_stream_info()) is None:
            err_msg = "Failed to get stream information"
            raise RuntimeError(err_msg)

        return stream_info

    async def stop_streaming(self) -> None:
        """Stop an active stream."""
        backend = self._require_streaming_backend()
        await self._acquire_lock()
        try:
            if self.stream.is_active:
                await backend.stop_stream()
                self.stream_service.reset()
            else:
                err_msg = "No stream active"
                raise RuntimeError(err_msg)
        finally:
            self.lock.release()

    async def cleanup(self, *, force: bool = False) -> None:
        """Clean up camera and streaming resources. If force is True, this happens even if there is an active stream."""
        if self.stream.is_active and not force:
            raise ActiveStreamError(self.stream)

        if self.stream.is_active:
            await self.stop_streaming()

        await clear_directory(settings.image_path, time_to_live_s=settings.image_ttl_s)

        await self._acquire_lock()
        try:
            await self.backend.cleanup()
        finally:
            self.lock.release()

    async def get_stream_info(self) -> StreamView | None:
        """Get stream information including metadata if active."""
        if self.stream.is_active:
            backend = self._require_streaming_backend()
            camera_properties, capture_metadata = await backend.get_stream_metadata()
            return self.stream_service.build_view(
                camera_properties=camera_properties,
                capture_metadata=capture_metadata,
            )
        # Return empty stream view if no stream is active
        return None

    async def get_status(self) -> CameraStatusView:
        """Return the current camera status including active stream info."""
        stream_info = await self.get_stream_info()
        return CameraStatusView(
            current_mode=self.backend.current_mode,
            stream=stream_info if self.stream.is_active else None,
        )
