"""Background loop that hibernates the lores preview encoder when nothing's watching.

The lores H264 encoder is the Pi's only preview surface — it publishes RTSP
to the local MediaMTX sidecar, which then serves LL-HLS to browsers and
native clients via the backend's HLS proxy. Keeping the encoder running
costs ~3% of a Pi 5 core, which is cheap enough to ignore for active
sessions but adds up over the lifetime of an idle device.

The sleeper arbitrates between three signals to decide whether the encoder
should be running:

1. **Relay connectivity.** If the Pi is supposed to talk to a backend and
   the WebSocket relay is currently down (or was never up), nobody can reach
   us — the encoder goes to sleep until the relay comes back.
2. **Relay idle timer.** If the relay is connected but hasn't seen any
   commands for ``preview_hibernate_after_s`` seconds, the encoder sleeps.
   Any incoming command (including the HLS segment proxy calls) wakes it
   back up.
3. **Standalone mode.** When the plugin is not configured with a pairing
   backend at all (``settings.relay_enabled`` is false and pairing is not in
   progress), the sleeper stays idle and the encoder runs continuously so
   users on the same LAN can hit MediaMTX directly.

``preview_hibernate_after_s = 0`` disables hibernation entirely — the
encoder runs as long as the app process does, regardless of relay state.
Tests rely on this to keep the lifespan simple.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.api.services.preview_pipeline import PreviewPipelineManager, get_preview_pipeline_manager
from app.core.config import settings
from app.utils.relay_state import is_relay_connected, seconds_since_last_activity

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.api.services.hardware_protocols import Picamera2Like

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 15.0


class PreviewSleeper:
    """Background task that toggles the lores preview encoder based on relay activity."""

    def __init__(
        self,
        *,
        pipeline: PreviewPipelineManager | None = None,
        camera_getter: Callable[[], Picamera2Like | None] | None = None,
        hibernate_after_s: float | None = None,
        poll_interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        self._pipeline = pipeline or get_preview_pipeline_manager()
        self._camera_getter = camera_getter
        self._hibernate_after_s = (
            hibernate_after_s if hibernate_after_s is not None else settings.preview_hibernate_after_s
        )
        self._poll_interval_s = poll_interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self, camera_getter: Callable[[], Picamera2Like | None]) -> None:
        """Kick off the background task. Idempotent."""
        if self._task is not None:
            return
        self._camera_getter = camera_getter
        self._task = asyncio.create_task(self._run(), name="preview_sleeper")

    async def stop(self) -> None:
        """Cancel the background task and wait for it to exit."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    def should_be_running(self) -> bool:
        """Decide whether the encoder should currently be running."""
        # Hibernation disabled entirely (or we're in standalone mode with no
        # relay): always-on. ``relay_enabled`` is true once pairing credentials
        # are on disk — before that we're in pairing mode and nobody is
        # watching anyway, so we sleep.
        if self._hibernate_after_s <= 0:
            return True

        if not settings.relay_enabled:
            # Pairing mode or standalone. In standalone mode users reach the
            # preview directly via the Pi's LAN IP and we can't tell if
            # they're watching — prefer keeping it off until they explicitly
            # pair. The always-on case is ``hibernate_after_s = 0`` above.
            return False

        if not is_relay_connected():
            return False

        idle = seconds_since_last_activity()
        if idle is None:
            # Connected but haven't seen a real command yet — keep the
            # encoder off until a user actually opens the mosaic.
            return False
        return idle <= self._hibernate_after_s

    async def _run(self) -> None:
        """Poll at ``_poll_interval_s`` and toggle the encoder to match."""
        try:
            while True:
                await self._tick()
                await asyncio.sleep(self._poll_interval_s)
        except asyncio.CancelledError:
            # On cancel, stop the encoder if it's running so the app shutdown
            # path doesn't leave a dangling ffmpeg subprocess. Any errors are
            # swallowed because we're already tearing down.
            camera = self._camera_getter() if self._camera_getter else None
            if camera is not None and self._pipeline.is_running:
                try:
                    await self._pipeline.stop(camera)
                except Exception:  # noqa: BLE001
                    logger.debug("Preview sleeper cleanup error on cancel", exc_info=True)
            raise

    async def _tick(self) -> None:
        camera = self._camera_getter() if self._camera_getter else None
        if camera is None:
            # No camera yet (e.g. initial startup race). Try again next tick.
            return

        desired = self.should_be_running()
        currently_running = self._pipeline.is_running

        if desired and not currently_running:
            logger.info("Preview sleeper: waking encoder (relay activity resumed)")
            try:
                await self._pipeline.start(camera)
            except RuntimeError as exc:
                logger.warning("Preview sleeper failed to wake encoder: %s", exc)
            return

        if not desired and currently_running:
            logger.info(
                "Preview sleeper: hibernating encoder (relay idle or disconnected; hibernate_after=%.0fs)",
                self._hibernate_after_s,
            )
            try:
                await self._pipeline.stop(camera)
            except RuntimeError as exc:
                logger.warning("Preview sleeper failed to hibernate encoder: %s", exc)


_singleton: PreviewSleeper | None = None


def get_preview_sleeper() -> PreviewSleeper:
    """Return the process-wide preview sleeper."""
    global _singleton  # noqa: PLW0603
    if _singleton is None:
        _singleton = PreviewSleeper()
    return _singleton


def reset_preview_sleeper() -> None:
    """Reset the singleton (tests only)."""
    global _singleton  # noqa: PLW0603
    _singleton = None
