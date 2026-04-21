"""Runtime-owned cached preview-thumbnail maintenance."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from app.backend.client import BackendUploadError, upload_preview_thumbnail
from app.core.config import settings
from app.observability.logging import build_log_extra

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from app.camera.services.manager import CameraManager
    from app.relay.state import RelayRuntimeState

logger = logging.getLogger(__name__)

_STARTUP_DELAY_S = 5.0
_REFRESH_INTERVAL_S = 600.0
_POLL_INTERVAL_S = 30.0
_HLS_ACTIVITY_WINDOW_S = 45.0
_ACTIVITY_REFRESH_COOLDOWN_S = 60.0
_LOCK_TIMEOUT_S = 0.25
_STARTUP_REASON = "startup"
_INTERVAL_REASON = "interval"
_ACTIVITY_REASON = "activity"


class PreviewThumbnailWorker:
    """Maintain a cached preview thumbnail for the camera-card mosaic."""

    def __init__(
        self,
        *,
        camera_manager: CameraManager,
        relay_state: RelayRuntimeState,
        relay_enabled_getter: Callable[[], bool],
        cache_dir: Path | None = None,
        refresh_interval_s: float = _REFRESH_INTERVAL_S,
        poll_interval_s: float = _POLL_INTERVAL_S,
        activity_refresh_cooldown_s: float = _ACTIVITY_REFRESH_COOLDOWN_S,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._camera_manager = camera_manager
        self._relay_state = relay_state
        self._relay_enabled_getter = relay_enabled_getter
        self._cache_path = (cache_dir or settings.image_path / "preview-thumbnail") / "current.jpg"
        self._refresh_interval_s = refresh_interval_s
        self._poll_interval_s = poll_interval_s
        self._activity_refresh_cooldown_s = activity_refresh_cooldown_s
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_refresh_monotonic: float | None = None
        self._last_activity_refresh_monotonic: float | None = None

    @property
    def cache_path(self) -> Path:
        """Return the local cache path for the current preview thumbnail."""
        return self._cache_path

    async def run_forever(self) -> None:
        """Refresh the cached preview thumbnail on startup, interval, and preview activity."""
        await self._sleep(_STARTUP_DELAY_S)
        await self.refresh_once(reason=_STARTUP_REASON)
        while True:
            await self._maybe_refresh()
            await self._sleep(self._poll_interval_s)

    async def refresh_once(self, *, reason: str) -> bool:
        """Capture, cache, and optionally upload one preview thumbnail."""
        image_bytes = await self._camera_manager.capture_preview_thumbnail_jpeg(lock_timeout_s=_LOCK_TIMEOUT_S)
        if image_bytes is None:
            logger.debug(
                "Preview thumbnail refresh skipped (%s): camera busy or preview stream active",
                reason,
                extra=build_log_extra(),
            )
            return False

        _write_preview_thumbnail_atomic(self._cache_path, image_bytes)
        now = self._monotonic()
        self._last_refresh_monotonic = now
        if reason == _ACTIVITY_REASON:
            self._last_activity_refresh_monotonic = now

        if not self._relay_enabled_getter():
            logger.debug("Preview thumbnail refreshed locally (%s)", reason, extra=build_log_extra())
            return True

        try:
            await upload_preview_thumbnail(image_bytes=image_bytes)
        except BackendUploadError as exc:
            logger.debug(
                "Preview thumbnail upload skipped (%s): %s",
                reason,
                exc,
                extra=build_log_extra(),
            )
            return True

        logger.debug("Preview thumbnail refreshed and uploaded (%s)", reason, extra=build_log_extra())
        return True

    async def _maybe_refresh(self) -> None:
        if self._should_refresh_for_activity():
            await self.refresh_once(reason=_ACTIVITY_REASON)
            return
        if self._should_refresh_for_interval():
            await self.refresh_once(reason=_INTERVAL_REASON)

    def _should_refresh_for_interval(self) -> bool:
        if self._last_refresh_monotonic is None:
            return True
        return (self._monotonic() - self._last_refresh_monotonic) >= self._refresh_interval_s

    def _should_refresh_for_activity(self) -> bool:
        hls_idle = self._relay_state.seconds_since_last_hls_activity()
        if hls_idle is None or hls_idle > _HLS_ACTIVITY_WINDOW_S:
            return False
        if self._last_activity_refresh_monotonic is None:
            return True
        return (self._monotonic() - self._last_activity_refresh_monotonic) >= self._activity_refresh_cooldown_s


def _write_preview_thumbnail_atomic(path: Path, image_bytes: bytes) -> None:
    """Write preview thumbnail bytes atomically, keeping the previous file on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_bytes(image_bytes)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
