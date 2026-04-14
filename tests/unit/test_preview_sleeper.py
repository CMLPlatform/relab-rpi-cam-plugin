"""Tests for the preview hibernation sleeper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.services.preview_pipeline import PreviewPipelineManager
from app.core.config import settings
from app.utils import relay_state
from app.utils.preview_sleeper import PreviewSleeper


@pytest.fixture(autouse=True)
def _reset_relay_state() -> None:
    relay_state.reset_for_tests()


@pytest.fixture
def pipeline() -> MagicMock:
    p = MagicMock(spec=PreviewPipelineManager)
    p.is_running = False
    p.start = AsyncMock()
    p.stop = AsyncMock()
    return p


@pytest.fixture
def camera_getter() -> MagicMock:
    getter = MagicMock()
    getter.return_value = MagicMock(name="camera")
    return getter


class TestShouldBeRunning:
    """The encoder-state decision matrix."""

    def test_hibernate_disabled_means_always_on(self, pipeline: MagicMock) -> None:
        """``preview_hibernate_after_s = 0`` disables all hibernation."""
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=0)
        assert sleeper.should_be_running() is True

    def test_not_relay_enabled_means_hibernate(
        self,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pairing mode / pre-pairing: no relay = nobody's watching = sleep."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: False))
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        assert sleeper.should_be_running() is False

    def test_relay_disconnected_means_hibernate(
        self,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Configured but not currently connected — backend's unreachable."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        # Relay state reset by autouse fixture; no connect() called.
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        assert sleeper.should_be_running() is False

    def test_relay_connected_but_never_active_means_hibernate(
        self,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Connected but no commands yet — nobody's watching, stay asleep."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        # Artificially flip the connected flag without touching the activity timer.
        relay_state._connected = True  # noqa: SLF001
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        assert sleeper.should_be_running() is False

    def test_relay_connected_and_recent_activity_means_run(
        self,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Connected + command in the last minute = wake up."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        relay_state.mark_relay_connected()
        relay_state.mark_relay_activity()
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        assert sleeper.should_be_running() is True

    def test_relay_connected_but_idle_past_threshold_means_hibernate(
        self,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Connected but last command too long ago — sleep."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        relay_state.mark_relay_connected()
        relay_state.mark_relay_activity()
        # Force the monotonic clock forward by overriding the module-level timer.
        relay_state._last_activity_monotonic -= 600  # noqa: SLF001
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        assert sleeper.should_be_running() is False


class TestTick:
    """``_tick`` translates ``should_be_running`` into encoder start/stop calls."""

    async def test_tick_starts_encoder_when_desired_and_not_running(
        self,
        pipeline: MagicMock,
        camera_getter: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transition asleep → awake calls ``pipeline.start``."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        relay_state.mark_relay_connected()
        relay_state.mark_relay_activity()
        pipeline.is_running = False

        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        sleeper._camera_getter = camera_getter  # noqa: SLF001

        await sleeper._tick()  # noqa: SLF001

        pipeline.start.assert_awaited_once_with(camera_getter.return_value)
        pipeline.stop.assert_not_called()

    async def test_tick_stops_encoder_when_not_desired_and_running(
        self,
        pipeline: MagicMock,
        camera_getter: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transition awake → asleep calls ``pipeline.stop``."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        # Connected but idle: should_be_running → False
        relay_state.mark_relay_connected()
        relay_state.mark_relay_activity()
        relay_state._last_activity_monotonic -= 600  # noqa: SLF001
        pipeline.is_running = True

        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        sleeper._camera_getter = camera_getter  # noqa: SLF001

        await sleeper._tick()  # noqa: SLF001

        pipeline.stop.assert_awaited_once_with(camera_getter.return_value)
        pipeline.start.assert_not_called()

    async def test_tick_is_noop_when_no_camera(
        self,
        pipeline: MagicMock,
    ) -> None:
        """No camera primed yet — skip the tick without touching the pipeline."""
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=0)
        sleeper._camera_getter = lambda: None  # noqa: SLF001

        await sleeper._tick()  # noqa: SLF001

        pipeline.start.assert_not_called()
        pipeline.stop.assert_not_called()
