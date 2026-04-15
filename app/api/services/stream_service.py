"""Focused stream state and view-building service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.api.services.camera_backend import StreamStartResult
from app.api.services.stream_state import ActiveStreamState

if TYPE_CHECKING:
    from typing import Any

    from relab_rpi_cam_models.stream import StreamView


class StreamService:
    """Own stream runtime state transitions and view construction."""

    def __init__(self) -> None:
        self._state = ActiveStreamState()

    @property
    def state(self) -> ActiveStreamState:
        """Expose the current active stream state."""
        return self._state

    def start(self, result: StreamStartResult) -> None:
        """Persist active stream state after a successful backend start."""
        self._state.mode = result.mode
        self._state.url = result.url
        self._state.started_at = datetime.now(UTC)

    def reset(self) -> None:
        """Clear the active stream state."""
        self._state = ActiveStreamState()

    def build_view(
        self,
        camera_properties: dict[str, Any],
        capture_metadata: dict[str, Any],
    ) -> StreamView | None:
        """Return the public stream view for the current state."""
        return self._state.to_view(camera_properties=camera_properties, capture_metadata=capture_metadata)
