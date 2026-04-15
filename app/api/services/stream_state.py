"""Plugin-local runtime state for active streams."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode, StreamView

from app.api.services.contract_adapters import build_stream_metadata


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

    def to_view(
        self,
        camera_properties: dict[str, Any],
        capture_metadata: dict[str, Any],
    ) -> StreamView | None:
        """Convert runtime state into the shared stream contract."""
        if not self.is_active:
            return None
        if self.mode is None:
            raise StreamStateError(msg="Stream mode is None but stream is marked as active")
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
