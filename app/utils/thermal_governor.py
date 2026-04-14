"""Thermal governor for the lores preview encoder.

Monitors the Pi's CPU temperature (via the existing telemetry collector) and
drops the lores preview bitrate when the SoC runs hot, restoring it once it
cools. Uses hysteresis so we don't flap across the threshold when temps sit
right at the boundary.

Design notes:
- Only the lores preview encoder is governed. The main-stream YouTube
  encoder is left alone: a user streaming to YouTube has a specific
  bitrate target and we don't silently change it out from under them.
- Hysteresis bands: drop at >80°C sustained 10s, restore at <70°C sustained
  30s. Slow restore gives the SoC time to actually cool, avoiding oscillation.
- The governor uses ``asyncio.to_thread`` for nothing — everything it touches
  is already async or instant. It does, however, use ``asyncio.sleep`` for
  its poll loop.
- If telemetry collection fails (missing sysfs on a dev host, for example)
  the governor logs and backs off but does not crash the app.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from relab_rpi_cam_models.telemetry import ThermalState

from app.api.services.preview_pipeline import PreviewPipelineManager
from app.utils.telemetry import collect_telemetry

logger = logging.getLogger(__name__)

_DROP_TEMP_C = 80.0
_RESTORE_TEMP_C = 70.0
_SUSTAIN_DROP_SECONDS = 10.0
_SUSTAIN_RESTORE_SECONDS = 30.0
_POLL_INTERVAL_SECONDS = 5.0

_HIGH_BITRATE = 500_000  # 500 kbps — normal lores preview
_LOW_BITRATE = 200_000  # 200 kbps — throttled lores preview when hot


@dataclass
class GovernorState:
    """Rolling state used for hysteresis decisions."""

    throttled: bool = False
    over_threshold_since: float | None = None
    below_threshold_since: float | None = None
    last_snapshot_state: ThermalState = field(default=ThermalState.NORMAL)


class ThermalGovernor:
    """Hysteresis-based CPU temperature watchdog for the preview encoder."""

    def __init__(
        self,
        pipeline: PreviewPipelineManager,
        *,
        drop_temp_c: float = _DROP_TEMP_C,
        restore_temp_c: float = _RESTORE_TEMP_C,
        sustain_drop_s: float = _SUSTAIN_DROP_SECONDS,
        sustain_restore_s: float = _SUSTAIN_RESTORE_SECONDS,
        poll_interval_s: float = _POLL_INTERVAL_SECONDS,
        high_bitrate: int = _HIGH_BITRATE,
        low_bitrate: int = _LOW_BITRATE,
    ) -> None:
        self._pipeline = pipeline
        self._drop_temp_c = drop_temp_c
        self._restore_temp_c = restore_temp_c
        self._sustain_drop_s = sustain_drop_s
        self._sustain_restore_s = sustain_restore_s
        self._poll_interval_s = poll_interval_s
        self._high_bitrate = high_bitrate
        self._low_bitrate = low_bitrate
        self._state = GovernorState()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._camera_getter: "callable[[], object] | None" = None  # type: ignore[name-defined]

    @property
    def is_throttled(self) -> bool:
        """Whether the governor currently holds the encoder at the low bitrate."""
        return self._state.throttled

    def start(self, camera_getter: "callable[[], object]") -> None:  # type: ignore[name-defined]
        """Start the governor's background polling task.

        ``camera_getter`` is a zero-arg callable returning the live Picamera2
        handle (or None if the camera isn't initialised). Calling a getter
        avoids stashing a Camera reference that might go stale on cleanup.
        """
        if self._task is not None and not self._task.done():
            return
        self._camera_getter = camera_getter
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="thermal-governor")

    async def stop(self) -> None:
        """Cancel the background task."""
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Thermal governor tick failed; continuing")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_s)
            except TimeoutError:
                continue

    async def _tick(self) -> None:
        """Evaluate temperature and toggle bitrate if needed."""
        snapshot = await collect_telemetry()
        temp = snapshot.cpu_temp_c
        self._state.last_snapshot_state = snapshot.thermal_state
        if temp is None:
            return

        loop = asyncio.get_running_loop()
        now = loop.time()

        if temp >= self._drop_temp_c:
            self._state.below_threshold_since = None
            if self._state.over_threshold_since is None:
                self._state.over_threshold_since = now
            if (
                not self._state.throttled
                and now - self._state.over_threshold_since >= self._sustain_drop_s
            ):
                await self._apply_bitrate(self._low_bitrate)
                self._state.throttled = True
                logger.warning(
                    "Thermal governor dropped lores preview bitrate to %d bps (CPU %.1f°C)",
                    self._low_bitrate,
                    temp,
                )
            return

        if temp <= self._restore_temp_c:
            self._state.over_threshold_since = None
            if self._state.below_threshold_since is None:
                self._state.below_threshold_since = now
            if (
                self._state.throttled
                and now - self._state.below_threshold_since >= self._sustain_restore_s
            ):
                await self._apply_bitrate(self._high_bitrate)
                self._state.throttled = False
                logger.info(
                    "Thermal governor restored lores preview bitrate to %d bps (CPU %.1f°C)",
                    self._high_bitrate,
                    temp,
                )
            return

        # In the hysteresis band — reset both sustain timers so we need a
        # fresh sustained excursion before the next toggle.
        self._state.over_threshold_since = None
        self._state.below_threshold_since = None

    async def _apply_bitrate(self, bitrate: int) -> None:
        if self._camera_getter is None:
            return
        camera = self._camera_getter()
        if camera is None:
            return
        await self._pipeline.set_bitrate(camera, bitrate)  # type: ignore[arg-type]


_singleton: ThermalGovernor | None = None


def get_thermal_governor() -> ThermalGovernor:
    """Return the process-wide thermal governor, creating it on first call."""
    global _singleton  # noqa: PLW0603
    if _singleton is None:
        from app.api.services.preview_pipeline import get_preview_pipeline_manager  # noqa: PLC0415

        _singleton = ThermalGovernor(get_preview_pipeline_manager())
    return _singleton


def reset_thermal_governor() -> None:
    """Reset the singleton (tests only)."""
    global _singleton  # noqa: PLW0603
    _singleton = None
