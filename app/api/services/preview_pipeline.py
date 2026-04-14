"""Lores-stream preview pipeline: publishes to the local MediaMTX sidecar.

Architecture (Phase 6B):

    picamera2 (lores 640x480) ──H264Encoder──FfmpegOutput──▶ rtsp://mediamtx/cam-preview
                                                                      │
                                                                      ▼
                                                           browser ◀─WHEP─ mediamtx:8889

This service manages the lifecycle of the lores H264 encoder and its
``FfmpegOutput``. It's independent of the main YouTube stream path (Phase 6B
stage 2): preview runs at ~500kbps on the lores buffer and stays cheap even
while the main encoder is pushing 4Mbps to YouTube, because the two encoders
read from different picamera2 stream buffers.

The service is started on demand — typically by the WHEP proxy router when
the first browser WHEP session opens — and stopped when the last subscriber
leaves. Start/stop is reference-counted so multiple concurrent sessions share
one encoder instance.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from app.api.services.hardware_protocols import Picamera2Like
from app.api.services.hardware_stubs import FfmpegOutputStub, H264EncoderStub

if TYPE_CHECKING:
    from picamera2.encoders import H264Encoder
    from picamera2.outputs import FfmpegOutput
else:
    try:
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import FfmpegOutput
    except ImportError:
        H264Encoder = H264EncoderStub
        FfmpegOutput = FfmpegOutputStub

logger = logging.getLogger(__name__)

# Default MediaMTX ingest target. Resolves through docker's host-gateway since
# the app container is on the bridge network and MediaMTX runs on the host
# network (see compose.yml).
DEFAULT_MEDIAMTX_URL = "rtsp://host.docker.internal:8554/cam-preview"
_DEFAULT_LORES_BITRATE = 500_000


def _build_ffmpeg_output(target_url: str) -> object:
    """Build a FfmpegOutput that RTSP-publishes lores H264 to MediaMTX."""
    return FfmpegOutput(f"-c:v copy -f rtsp -rtsp_transport tcp {target_url}")


class PreviewPipelineManager:
    """Lifecycle manager for the lores-stream preview H264 encoder."""

    def __init__(
        self,
        *,
        target_url: str = DEFAULT_MEDIAMTX_URL,
        bitrate: int = _DEFAULT_LORES_BITRATE,
    ) -> None:
        self._target_url = target_url
        self._bitrate = bitrate
        self._encoder: H264Encoder | None = None
        self._subscribers = 0
        self._lock = asyncio.Lock()

    @property
    def active_subscribers(self) -> int:
        """Number of clients currently holding the pipeline open."""
        return self._subscribers

    @property
    def is_running(self) -> bool:
        """Whether the encoder is attached to the lores stream."""
        return self._encoder is not None

    async def acquire(self, camera: Picamera2Like) -> None:
        """Start the encoder on first acquire, otherwise just increment ref count."""
        async with self._lock:
            self._subscribers += 1
            if self._encoder is None:
                await self._start(camera)

    async def release(self, camera: Picamera2Like) -> None:
        """Decrement ref count; stop the encoder once nobody holds it."""
        async with self._lock:
            if self._subscribers == 0:
                return
            self._subscribers -= 1
            if self._subscribers == 0 and self._encoder is not None:
                await self._stop(camera)

    async def force_stop(self, camera: Picamera2Like) -> None:
        """Shut down the encoder regardless of the ref count (used on cleanup/shutdown)."""
        async with self._lock:
            self._subscribers = 0
            if self._encoder is not None:
                await self._stop(camera)

    async def set_bitrate(self, camera: Picamera2Like, bitrate: int) -> None:
        """Swap the active encoder with one at a new bitrate.

        Used by the thermal governor: when the Pi runs hot, drop the preview
        bitrate so software H264 encoding costs less CPU. Does nothing if the
        encoder isn't currently running.
        """
        async with self._lock:
            self._bitrate = bitrate
            if self._encoder is None:
                return
            logger.info("Reconfiguring lores preview encoder to %d bps", bitrate)
            await self._stop(camera)
            await self._start(camera)

    async def _start(self, camera: Picamera2Like) -> None:
        logger.info("Starting lores preview pipeline → %s @ %d bps", self._target_url, self._bitrate)
        encoder = cast("H264Encoder", H264Encoder(bitrate=self._bitrate))
        output = _build_ffmpeg_output(self._target_url)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(camera.start_encoder, encoder, output, name="lores"),
                timeout=30.0,
            )
        except TimeoutError as exc:
            msg = "Preview pipeline ffmpeg startup timeout"
            raise RuntimeError(msg) from exc
        except (OSError, RuntimeError) as exc:
            msg = f"Preview pipeline failed to start: {exc}"
            raise RuntimeError(msg) from exc
        self._encoder = encoder

    async def _stop(self, camera: Picamera2Like) -> None:
        logger.info("Stopping lores preview pipeline")
        try:
            await asyncio.to_thread(camera.stop_encoder, name="lores")
        except (OSError, RuntimeError) as exc:
            logger.warning("Preview pipeline stop had a non-fatal error: %s", exc)
        self._encoder = None


_singleton: PreviewPipelineManager | None = None


def get_preview_pipeline_manager() -> PreviewPipelineManager:
    """Return the process-wide preview pipeline manager."""
    global _singleton  # noqa: PLW0603
    if _singleton is None:
        _singleton = PreviewPipelineManager()
    return _singleton


def reset_preview_pipeline_manager() -> None:
    """Reset the singleton (tests only)."""
    global _singleton  # noqa: PLW0603
    _singleton = None
