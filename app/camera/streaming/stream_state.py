"""Plugin-local runtime state for active streams."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode, StreamView

from app.backend.contract_adapters import build_stream_metadata

if TYPE_CHECKING:
    from app.camera.services.backend import StreamStartResult


class StreamStateError(Exception):
    """Raised when plugin runtime stream state is inconsistent."""

    def __init__(self, msg: str | None = None) -> None:
        super().__init__(msg or "Stream state is inconsistent.")


@dataclass
class ActiveStreamState:
    """Plugin-owned runtime state for the currently active stream."""

    mode: StreamMode | None = None
    url: AnyUrl | None = None
    started_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """Return True when the plugin currently has an active stream."""
        return self.mode is not None

    def start(self, result: StreamStartResult) -> None:
        """Persist active stream state after a successful backend start."""
        self.mode = result.mode
        self.url = result.url
        self.started_at = datetime.now(UTC)

    def reset(self) -> None:
        """Clear the active stream state."""
        self.mode = None
        self.url = None
        self.started_at = None

    def to_view(
        self,
        camera_properties: dict[str, Any],
        capture_metadata: dict[str, Any],
    ) -> StreamView | None:
        """Convert runtime state into the shared stream contract."""
        if self.mode is None:
            return None
        if self.url is None:
            raise StreamStateError(msg="Stream URL is None but stream is marked as active")
        if self.started_at is None:
            raise StreamStateError(msg="Stream start time is None but stream is marked as active")

        return StreamView(
            mode=self.mode,
            provider=self.mode.value,
            url=self.url,
            started_at=self.started_at,
            metadata=build_stream_metadata(camera_properties, capture_metadata),
        )
