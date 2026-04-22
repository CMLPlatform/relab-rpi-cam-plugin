"""Main camera manager service class."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from io import BytesIO
from typing import TYPE_CHECKING

from relab_rpi_cam_models.camera import CameraMode, CameraStatusView
from relab_rpi_cam_models.images import ImageCaptureResponse, ImageCaptureStatus

from app.backend.contract_adapters import build_image_metadata, image_metadata_to_exif
from app.backend.factory import create_camera_backend
from app.camera.exceptions import ActiveStreamError
from app.camera.schemas import (
    CameraControlsPatch,
    CameraControlsView,
    FocusControlRequest,
    YoutubeStreamConfig,
)
from app.camera.services.backend import CameraBackend, ControllableCameraBackend, StreamingCameraBackend
from app.core.settings import settings
from app.image_sinks import ImageSink, ImageSinkError, get_image_sink
from app.media.stream_service import StreamService
from app.media.stream_state import ActiveStreamState
from app.observability.logging import build_log_extra
from app.upload.queue import UploadQueue
from app.utils.files import clear_directory

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
    from pathlib import Path

    from PIL.Image import Exif
    from PIL.Image import Image as PilImage
    from relab_rpi_cam_models.stream import StreamMode, StreamView

logger = logging.getLogger(__name__)


class StreamingNotSupportedError(RuntimeError):
    """Raised when a streaming operation is attempted on a non-streaming backend."""

    def __init__(self, backend: CameraBackend) -> None:
        super().__init__(f"Backend {type(backend).__name__} does not support live streaming")


class CameraControlsNotSupportedError(RuntimeError):
    """Raised when a controls operation is attempted on a non-controllable backend."""

    def __init__(self, backend: CameraBackend) -> None:
        super().__init__(f"Backend {type(backend).__name__} does not support remote camera controls")


def _unlink_quiet(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _encode_jpeg_atomic(
    image: PilImage,
    image_path: Path,
    exif: Exif,
) -> bytes:
    """Encode a PIL image to ``image_path`` atomically and return the bytes.

    Writes to a sibling ``.tmp`` file first then ``os.replace``s it onto the final
    name, so a disk-full, crash, or concurrent reader never sees a half-written JPEG.
    Runs off the event loop via ``asyncio.to_thread``.
    """
    tmp_path = image_path.with_suffix(image_path.suffix + ".tmp")
    try:
        image.save(tmp_path, exif=exif, format="JPEG", quality=90)
        image_bytes = tmp_path.read_bytes()
        tmp_path.replace(image_path)
    except BaseException:
        _unlink_quiet(tmp_path)
        raise
    return image_bytes


def encode_preview_jpeg(image: PilImage) -> bytes:
    """Encode a smaller JPEG suitable for cached preview thumbnails."""
    frame = image.copy()
    frame.thumbnail((640, 400))
    buffer = BytesIO()
    frame.save(buffer, format="JPEG", quality=72, optimize=True)
    return buffer.getvalue()


class CameraManager:
    """Main camera manager class which handles camera setup, streaming, and cleanup."""

    def __init__(
        self,
        backend: CameraBackend | None = None,
        upload_queue: UploadQueue | None = None,
        sink: ImageSink | None = None,
    ) -> None:
        self.backend = backend or create_camera_backend()
        # The image sink is resolved lazily on first ``capture_jpeg`` so that
        # instantiating a ``CameraManager`` (e.g. the module-level singleton
        # in ``dependencies/camera_management.py``) doesn't fire factory
        # validation errors when imports happen before env is loaded.
        self._sink: ImageSink | None = sink
        self._upload_queue_override = upload_queue
        self._upload_queue: UploadQueue | None = None
        self._on_capture_uploaded: Callable[[PilImage], Awaitable[None]] | None = None
        self.stream_service = StreamService()
        self.lock = asyncio.Lock()
        self.lock_timeout = 10

    def set_capture_uploaded_hook(
        self,
        hook: Callable[[PilImage], Awaitable[None]] | None,
    ) -> None:
        """Register a best-effort callback invoked with the PIL frame after a successful capture upload."""
        self._on_capture_uploaded = hook

    @property
    def sink(self) -> ImageSink:
        """Return the configured image sink, resolving lazily on first access."""
        if self._sink is None:
            self._sink = get_image_sink(settings)
        return self._sink

    @property
    def upload_queue(self) -> UploadQueue:
        """Return the upload queue, creating it on first access with the active sink."""
        if self._upload_queue is None:
            self._upload_queue = self._upload_queue_override or UploadQueue(
                settings.image_path / "queue",
                sink=self.sink,
            )
        return self._upload_queue

    @property
    def stream(self) -> ActiveStreamState:
        """Expose active stream state for existing callers."""
        return self.stream_service.state

    def has_active_stream(self) -> bool:
        """Whether a stream is currently active."""
        return self.stream.is_active

    @contextlib.asynccontextmanager
    async def _locked(self, timeout_s: float | None = None) -> AsyncIterator[None]:
        """Acquire the camera lock with a timeout, then yield inside the critical section.

        Callers should use ``async with self._locked():`` so exceptions, early returns,
        and cancellation all release the lock automatically.
        """
        acquired = False
        try:
            async with asyncio.timeout(self.lock_timeout if timeout_s is None else timeout_s):
                await self.lock.acquire()
                acquired = True
        except TimeoutError as e:
            err_msg = f"Failed to acquire camera lock - timeout error: {e}"
            raise RuntimeError(err_msg) from e
        try:
            yield
        finally:
            if acquired:
                self.lock.release()

    async def setup_camera(self, mode: CameraMode) -> None:
        """Prepare the configured backend for the requested camera mode."""
        async with self._locked():
            await self.backend.open(mode)

    async def capture_jpeg(
        self,
        upload_metadata: Mapping[str, object] | None = None,
    ) -> ImageCaptureResponse:
        """Capture a still, push it to the backend, and fall back to the local queue on failure.

        This is a pure push flow: the Pi never serves captured bytes via HTTP. On
        a successful synchronous upload the local file is deleted. On failure the
        file is moved into the upload queue for exponential-backoff retry.

        The camera lock is held across the entire frame-to-disk path (capture, encode,
        atomic rename) so a concurrent stream start cannot race the encoder or truncate
        the JPEG on the way out.
        """
        upload_meta = upload_metadata or {}

        image_id = uuid.uuid4().hex
        filename = f"{image_id}.jpg"
        image_path = settings.image_path / filename

        async with self._locked():
            result = await self.backend.capture_image()
            img_metadata = build_image_metadata(result.image, result.camera_properties, result.capture_metadata)
            image_bytes = await asyncio.to_thread(
                _encode_jpeg_atomic,
                result.image,
                image_path,
                image_metadata_to_exif(img_metadata),
            )

        capture_metadata_dict = img_metadata.model_dump(mode="json")

        try:
            stored = await self.sink.put(
                image_id=image_id,
                image_bytes=image_bytes,
                filename=filename,
                capture_metadata=capture_metadata_dict,
                upload_metadata=upload_meta,
            )
        except ImageSinkError as exc:
            logger.warning(
                "Image sink for %s failed; enqueueing for retry: %s",
                image_id,
                exc,
                extra=build_log_extra(stream_mode=self.stream.mode),
            )
            await self.upload_queue.enqueue(
                image_id=image_id,
                image_path=image_path,
                filename=filename,
                capture_metadata=capture_metadata_dict,
                upload_metadata=upload_meta,
            )
            return ImageCaptureResponse(
                image_id=image_id,
                status=ImageCaptureStatus.QUEUED,
                metadata=img_metadata,
                image_url=None,
                expires_at=None,
            )

        await asyncio.to_thread(_unlink_quiet, image_path)

        if self._on_capture_uploaded is not None:
            try:
                await self._on_capture_uploaded(result.image)
            except Exception:
                logger.exception(
                    "Capture-uploaded hook failed",
                    extra=build_log_extra(stream_mode=self.stream.mode),
                )

        return ImageCaptureResponse(
            image_id=stored.image_id,
            status=ImageCaptureStatus.UPLOADED,
            metadata=img_metadata,
            image_url=stored.image_url,
            expires_at=stored.expires_at,
        )

    async def capture_preview_thumbnail_jpeg(
        self,
        *,
        lock_timeout_s: float = 0.25,
        preview_encoder_running: bool = False,
    ) -> bytes | None:
        """Capture a best-effort cached preview thumbnail without using still-capture flow.

        Returns ``None`` when the camera is busy, actively streaming, or otherwise
        unavailable for a cheap lores grab. This is intended for background cache
        maintenance only, never for request/response paths.

        When ``preview_encoder_running`` is True, the lores ring buffer is already
        active and owned by the encoder; tap it directly without acquiring the
        camera-manager lock (the lock serialises reconfiguration, not reads).
        """
        backend_camera = self.backend.camera
        if preview_encoder_running and backend_camera is not None:
            frame = await asyncio.to_thread(backend_camera.capture_image, "main")
            return await asyncio.to_thread(encode_preview_jpeg, frame)

        try:
            async with self._locked(timeout_s=lock_timeout_s):
                if self.stream.is_active:
                    return None

                if backend_camera is not None:
                    await self.backend.open(CameraMode.VIDEO)
                    frame = await asyncio.to_thread(backend_camera.capture_image, "main")
                else:
                    result = await self.backend.capture_image()
                    frame = result.image
                return await asyncio.to_thread(encode_preview_jpeg, frame)
        except RuntimeError:
            logger.debug("Preview thumbnail refresh skipped because the camera lock was busy")
            return None

    def _require_streaming_backend(self) -> StreamingCameraBackend:
        """Return the backend narrowed to StreamingCameraBackend, or raise."""
        if not isinstance(self.backend, StreamingCameraBackend):
            raise StreamingNotSupportedError(self.backend)
        return self.backend

    def _require_controllable_backend(self) -> ControllableCameraBackend:
        """Return the backend narrowed to ControllableCameraBackend, or raise."""
        if not isinstance(self.backend, ControllableCameraBackend):
            raise CameraControlsNotSupportedError(self.backend)
        return self.backend

    async def get_controls(self) -> CameraControlsView:
        """Return supported controls for the active backend."""
        backend = self._require_controllable_backend()
        async with self._locked():
            return await backend.get_controls()

    async def set_controls(self, patch: CameraControlsPatch) -> CameraControlsView:
        """Apply backend-native controls through the active backend."""
        backend = self._require_controllable_backend()
        async with self._locked():
            return await backend.set_controls(patch.controls)

    async def set_focus(self, request: FocusControlRequest) -> CameraControlsView:
        """Apply friendly focus controls through the active backend."""
        backend = self._require_controllable_backend()
        async with self._locked():
            return await backend.set_focus(request)

    async def start_streaming(
        self,
        mode: StreamMode,
        *,
        youtube_config: YoutubeStreamConfig | None = None,
    ) -> StreamView:
        """Start streaming for the requested provider/mode.

        The ``is_active`` check is performed inside the lock so two concurrent
        ``start_streaming`` calls cannot both pass the early-return guard and
        race to start the backend.
        """
        backend = self._require_streaming_backend()

        async with self._locked():
            if self.stream.is_active:
                raise ActiveStreamError(self.stream)
            try:
                logger.info("Starting stream", extra=build_log_extra(stream_mode=mode))
                result = await backend.start_stream(mode, youtube_config=youtube_config)
                self.stream_service.start(result)
            except Exception:
                self.stream_service.reset()
                raise

        if (stream_info := await self.get_stream_info()) is None:
            err_msg = "Failed to get stream information"
            raise RuntimeError(err_msg)

        return stream_info

    async def stop_streaming(self) -> None:
        """Stop an active stream."""
        backend = self._require_streaming_backend()
        async with self._locked():
            if not self.stream.is_active:
                err_msg = "No stream active"
                raise RuntimeError(err_msg)
            logger.info("Stopping stream", extra=build_log_extra(stream_mode=self.stream.mode))
            await backend.stop_stream()
            self.stream_service.reset()

    async def cleanup(self, *, force: bool = False) -> None:
        """Clean up camera and streaming resources. If force is True, this happens even if there is an active stream."""
        if self.stream.is_active and not force:
            raise ActiveStreamError(self.stream)

        if self.stream.is_active:
            await self.stop_streaming()

        await clear_directory(settings.image_path, time_to_live_s=settings.image_ttl_s)

        async with self._locked():
            await self.backend.cleanup()

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

    async def get_stream_view(self) -> StreamView | None:
        """Return the public stream view for the current state."""
        return await self.get_stream_info()

    async def get_status(self) -> CameraStatusView:
        """Return the current camera status including active stream info."""
        stream_info = await self.get_stream_info()
        return CameraStatusView(
            current_mode=self.backend.current_mode,
            stream=stream_info if self.stream.is_active else None,
        )

    async def get_camera_status(self) -> CameraStatusView:
        """Return the public camera status view."""
        return await self.get_status()
