"""Process-local signals published by the relay client.

The relay client (``app.utils.relay``) is an outbound WebSocket that either
sits connected-but-idle for most of the day or handles bursts of commands
when a user opens the mosaic / camera detail screen. Other services (the
preview sleeper, telemetry, tests) want to know two cheap things about it:

1. **Is the relay currently connected?** — used to gate work that doesn't
   matter when nobody can reach us (e.g. the lores preview encoder).
2. **When did we last see any activity on the relay?** — used to hibernate
   the preview encoder after a configurable idle window.

This module owns those two scalars behind module-level globals + thin
accessors. No locks: the writes are single monotonic timestamps and single
bools, and async single-threaded code doesn't need CAS semantics.
"""

from __future__ import annotations

import time

_connected: bool = False
_last_activity_monotonic: float | None = None
_last_hls_activity_monotonic: float | None = None


def mark_relay_connected() -> None:
    """Called by the relay client on a successful WebSocket handshake."""
    global _connected, _last_activity_monotonic  # noqa: PLW0603
    _connected = True
    _last_activity_monotonic = time.monotonic()


def mark_relay_disconnected() -> None:
    """Called when the WebSocket closes or the loop exits."""
    global _connected  # noqa: PLW0603
    _connected = False


def mark_relay_activity() -> None:
    """Called on every inbound command — resets the idle timer."""
    global _last_activity_monotonic  # noqa: PLW0603
    _last_activity_monotonic = time.monotonic()


def mark_hls_activity() -> None:
    """Called on every local HLS segment/playlist fetch — resets the HLS idle timer."""
    global _last_hls_activity_monotonic  # noqa: PLW0603
    _last_hls_activity_monotonic = time.monotonic()


def is_relay_connected() -> bool:
    """Whether the relay currently holds an open WebSocket to the backend."""
    return _connected


def seconds_since_last_activity() -> float | None:
    """Monotonic seconds since the last inbound relay command. ``None`` if never."""
    if _last_activity_monotonic is None:
        return None
    return time.monotonic() - _last_activity_monotonic


def seconds_since_last_hls_activity() -> float | None:
    """Monotonic seconds since the last local HLS fetch. ``None`` if never."""
    if _last_hls_activity_monotonic is None:
        return None
    return time.monotonic() - _last_hls_activity_monotonic


def reset_for_tests() -> None:
    """Reset module globals (tests only)."""
    global _connected, _last_activity_monotonic, _last_hls_activity_monotonic  # noqa: PLW0603
    _connected = False
    _last_activity_monotonic = None
    _last_hls_activity_monotonic = None
