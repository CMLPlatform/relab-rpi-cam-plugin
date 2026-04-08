"""Tests for camera management dependencies."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.dependencies import camera_management as camera_deps
from app.core.config import settings


@pytest.fixture
def mock_camera_manager(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Return a patched camera manager with async mocks."""
    mgr = SimpleNamespace(
        stream=SimpleNamespace(is_active=False, started_at=None),
        cleanup=AsyncMock(),
        stop_streaming=AsyncMock(),
    )
    monkeypatch.setattr(camera_deps, "camera_manager", mgr)
    return mgr


class TestCameraToStandby:
    """Tests for camera_to_standby."""

    async def test_cleans_up_when_inactive(self, mock_camera_manager: SimpleNamespace) -> None:
        """Should call cleanup if the stream is not active."""
        await camera_deps.camera_to_standby()
        mock_camera_manager.cleanup.assert_awaited_once()

    async def test_skips_cleanup_when_active(self, mock_camera_manager: SimpleNamespace) -> None:
        """Should not call cleanup if the stream is active.

        It may be needed for an ongoing stream and we don't want to disrupt it.
        """
        mock_camera_manager.stream.is_active = True
        await camera_deps.camera_to_standby()
        mock_camera_manager.cleanup.assert_not_awaited()


class TestCheckStreamDuration:
    """Tests for check_stream_duration."""

    async def test_stops_overdue_stream(
        self,
        mock_camera_manager: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should stop the stream if it has been active longer than the configured maximum duration.

        This prevents runaway streaming sessions that could consume resources indefinitely.
        """
        monkeypatch.setattr(settings, "max_stream_duration_s", 10)
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.stream.started_at = datetime.now(UTC) - timedelta(seconds=20)

        await camera_deps.check_stream_duration()

        mock_camera_manager.stop_streaming.assert_awaited_once()

    async def test_ignores_recent_stream(
        self,
        mock_camera_manager: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should not stop the stream if it has been active for less than the configured maximum duration."""
        monkeypatch.setattr(settings, "max_stream_duration_s", 10)
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.stream.started_at = datetime.now(UTC)

        await camera_deps.check_stream_duration()

        mock_camera_manager.stop_streaming.assert_not_awaited()

    async def test_logs_runtime_error(
        self,
        mock_camera_manager: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should log a RuntimeError if stopping the stream fails, but not raise it."""
        monkeypatch.setattr(settings, "max_stream_duration_s", 10)
        mock_camera_manager.stream.is_active = True
        mock_camera_manager.stream.started_at = datetime.now(UTC) - timedelta(seconds=20)
        mock_camera_manager.stop_streaming.side_effect = RuntimeError("boom")

        await camera_deps.check_stream_duration()

        mock_camera_manager.stop_streaming.assert_awaited_once()
