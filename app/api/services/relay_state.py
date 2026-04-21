"""Process-local relay activity state."""

from __future__ import annotations

import time


class RelayRuntimeState:
    """Own relay connectivity and activity timestamps for one app runtime."""

    def __init__(self) -> None:
        self._connected = False
        self._last_activity_monotonic: float | None = None
        self._last_hls_activity_monotonic: float | None = None

    def mark_connected(self) -> None:
        """Record a successful WebSocket handshake."""
        self._connected = True
        self._last_activity_monotonic = time.monotonic()

    def mark_disconnected(self) -> None:
        """Record that the relay is currently disconnected."""
        self._connected = False

    def mark_activity(self) -> None:
        """Record inbound relay activity."""
        self._last_activity_monotonic = time.monotonic()

    def mark_hls_activity(self) -> None:
        """Record local HLS activity."""
        self._last_hls_activity_monotonic = time.monotonic()

    def is_connected(self) -> bool:
        """Whether the relay currently holds an open WebSocket."""
        return self._connected

    def seconds_since_last_activity(self) -> float | None:
        """Monotonic seconds since the last inbound relay command."""
        if self._last_activity_monotonic is None:
            return None
        return time.monotonic() - self._last_activity_monotonic

    def seconds_since_last_hls_activity(self) -> float | None:
        """Monotonic seconds since the last local HLS fetch."""
        if self._last_hls_activity_monotonic is None:
            return None
        return time.monotonic() - self._last_hls_activity_monotonic

    def reset(self) -> None:
        """Reset activity state. Useful in tests and on app restarts."""
        self._connected = False
        self._last_activity_monotonic = None
        self._last_hls_activity_monotonic = None
