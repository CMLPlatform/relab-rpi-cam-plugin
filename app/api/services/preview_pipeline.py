"""Lores-stream preview pipeline: publishes to the local MediaMTX sidecar.

Architecture (post-Phase-9):

    picamera2 (lores 640x480) ──H264Encoder──FfmpegOutput──▶ rtsp://mediamtx/cam-preview
                                                                      │
                                                                      ├──▶ LL-HLS to browsers (:8888)
                                                                      └──▶ (future) PeerTube / S3 archive

The encoder runs **always-on** for the lifetime of the app process. At
~500kbps on the lores buffer the CPU cost is negligible (~3% on a Pi 5,
measured in HW-3's thermal test), and the alternative — ref-counted
start/stop via HTTP hooks — added complexity and a cold-start lag on the
first viewer. Simpler: the Pi lights up MediaMTX when it comes up, MediaMTX
happily idles with a publisher and no subscribers, and the first LL-HLS
viewer gets a live stream within ~1-2s.

The ``ThermalGovernor`` (Phase 6B) still calls :meth:`set_bitrate` to swap
the running encoder for a lower-bitrate one when the SoC runs hot.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
from typing import TYPE_CHECKING, Protocol, cast

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


class _EncoderWithBitrate(Protocol):
    """Typing helper: runtime encoder that exposes a ``bitrate`` attribute."""

    bitrate: int


def _build_ffmpeg_output(target_url: str) -> object:
    """Build a FfmpegOutput that RTSP-publishes lores H264 to MediaMTX."""
    return FfmpegOutput(f"-c:v copy -f rtsp -rtsp_transport tcp {target_url}")


class PreviewPipelineManager:
    """Lifecycle manager for the lores-stream preview H264 encoder.

    Post-Phase-9 this is a much thinner wrapper: the encoder is always-on
    for the process lifetime. :meth:`start` / :meth:`stop` are idempotent
    and :meth:`set_bitrate` swaps the live encoder without losing viewers.
    """

    def __init__(
        self,
        *,
        target_url: str = DEFAULT_MEDIAMTX_URL,
        bitrate: int = _DEFAULT_LORES_BITRATE,
    ) -> None:
        self._target_url = target_url
        self._bitrate = bitrate
        self._encoder: H264Encoder | None = None
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """Whether the encoder is attached to the lores stream."""
        return self._encoder is not None

    async def start(self, camera: Picamera2Like) -> None:
        """Start the always-on lores preview encoder. Idempotent."""
        async with self._lock:
            if self._encoder is None:
                await self._start(camera)

    async def stop(self, camera: Picamera2Like) -> None:
        """Stop the lores preview encoder (used on cleanup/shutdown)."""
        async with self._lock:
            if self._encoder is not None:
                await self._stop(camera)

    async def set_bitrate(self, camera: Picamera2Like, bitrate: int) -> None:
        """Swap the active encoder with one at a new bitrate.

        Used by the thermal governor: when the Pi runs hot, drop the preview
        bitrate so software H264 encoding costs less CPU. If the encoder
        isn't currently running (e.g. startup race), the new bitrate is
        remembered and applied on the next :meth:`start` call.
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
        # The runtime H264Encoder sometimes accepts a bitrate kwarg, but the
        # bundled typing stubs don't expose that parameter. Create the encoder
        # without kwargs and set a runtime attribute for callers that expect it.
        encoder = H264Encoder()
        # Best-effort: the encoder may not expose an attribute setter. Cast
        # the runtime encoder to a Protocol exposing `bitrate` so type-checkers
        # accept the assignment while preserving safety.
        with contextlib.suppress(Exception):
            cast("_EncoderWithBitrate", encoder).bitrate = self._bitrate
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
            await asyncio.to_thread(camera.stop_encoder, self._encoder)
        except (OSError, RuntimeError) as exc:
            logger.warning("Preview pipeline stop had a non-fatal error: %s", exc)
        self._encoder = None


@functools.lru_cache(maxsize=1)
def get_preview_pipeline_manager() -> PreviewPipelineManager:
    """Return the process-wide preview pipeline manager."""
    return PreviewPipelineManager()


def reset_preview_pipeline_manager() -> None:
    """Reset the singleton (tests only)."""
    get_preview_pipeline_manager.cache_clear()
