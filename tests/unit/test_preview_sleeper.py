"""Tests for the preview hibernation sleeper."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.services.preview_pipeline import PreviewPipelineManager
from app.core.config import settings
from app.utils import relay_state
from app.utils.preview_sleeper import (
    PreviewSleeper,
    get_preview_sleeper,
    reset_preview_sleeper,
)


@pytest.fixture(autouse=True)
def _reset_relay_state() -> None:
    relay_state.reset_for_tests()


@pytest.fixture
def pipeline() -> MagicMock:
    """A mock PreviewPipelineManager with a mutable ``is_running`` flag."""
    p = MagicMock(spec=PreviewPipelineManager)
    p.is_running = False
    p.start = AsyncMock()
    p.stop = AsyncMock()
    return p


@pytest.fixture
def camera_getter() -> MagicMock:
    """A mock camera getter that returns a dummy camera object."""
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
        relay_state._connected = True
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
        monkeypatch.setattr("app.utils.preview_sleeper.seconds_since_last_activity", lambda: 301.0)
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        assert sleeper.should_be_running() is False

    def test_recent_local_hls_activity_keeps_encoder_awake(
        self,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Local HLS segment fetches keep the encoder awake even when relay is idle."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        # Relay is connected but idle past the threshold.
        relay_state.mark_relay_connected()
        monkeypatch.setattr("app.utils.preview_sleeper.seconds_since_last_activity", lambda: 301.0)
        # But someone is actively fetching HLS segments locally.
        relay_state.mark_hls_activity()
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        assert sleeper.should_be_running() is True

    def test_local_hls_activity_keeps_encoder_awake_without_relay(
        self,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Local HLS keeps the encoder awake even when the relay is disconnected."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        # Relay not connected at all.
        relay_state.mark_hls_activity()
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        assert sleeper.should_be_running() is True

    def test_stale_hls_activity_does_not_keep_encoder_awake(
        self,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HLS activity older than the threshold does not prevent hibernation."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        monkeypatch.setattr("app.utils.preview_sleeper.seconds_since_last_hls_activity", lambda: 301.0)
        monkeypatch.setattr("app.utils.preview_sleeper.seconds_since_last_activity", lambda: 301.0)
        relay_state.mark_relay_connected()
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
        sleeper._camera_getter = camera_getter

        await sleeper._tick()

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
        monkeypatch.setattr("app.utils.preview_sleeper.seconds_since_last_activity", lambda: 301.0)
        pipeline.is_running = True

        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        sleeper._camera_getter = camera_getter

        await sleeper._tick()

        pipeline.stop.assert_awaited_once_with(camera_getter.return_value)
        pipeline.start.assert_not_called()

    async def test_tick_is_noop_when_no_camera(
        self,
        pipeline: MagicMock,
    ) -> None:
        """No camera primed yet — skip the tick without touching the pipeline."""
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=0)
        sleeper._camera_getter = lambda: None

        await sleeper._tick()

        pipeline.start.assert_not_called()
        pipeline.stop.assert_not_called()

    async def test_tick_swallows_start_runtime_errors(
        self,
        pipeline: MagicMock,
        camera_getter: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A RuntimeError from pipeline.start is logged, not propagated."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        relay_state.mark_relay_connected()
        relay_state.mark_relay_activity()
        pipeline.is_running = False
        pipeline.start.side_effect = RuntimeError("encoder busy")

        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        sleeper._camera_getter = camera_getter

        # Should not raise — the sleeper absorbs encoder errors so the
        # background task keeps polling.
        await sleeper._tick()
        pipeline.start.assert_awaited_once()

    async def test_tick_swallows_stop_runtime_errors(
        self,
        pipeline: MagicMock,
        camera_getter: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A RuntimeError from pipeline.stop is logged, not propagated."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        relay_state.mark_relay_connected()
        monkeypatch.setattr("app.utils.preview_sleeper.seconds_since_last_activity", lambda: 301.0)
        pipeline.is_running = True
        pipeline.stop.side_effect = RuntimeError("ffmpeg already dead")

        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=300)
        sleeper._camera_getter = camera_getter

        await sleeper._tick()
        pipeline.stop.assert_awaited_once()


class TestStartStopLifecycle:
    """``start`` and ``stop`` manage the background task handle."""

    async def test_start_is_idempotent(
        self,
        pipeline: MagicMock,
        camera_getter: MagicMock,
    ) -> None:
        """Calling ``start`` twice should not spawn a second task."""
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=0, poll_interval_s=0.01)
        sleeper.start(camera_getter)
        first_task = sleeper._task
        sleeper.start(camera_getter)  # second call is a noop
        assert sleeper._task is first_task
        await sleeper.stop()

    async def test_stop_is_noop_when_never_started(self, pipeline: MagicMock) -> None:
        """Calling ``stop`` without ``start`` is safe."""
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=0)
        await sleeper.stop()
        pipeline.start.assert_not_called()
        pipeline.stop.assert_not_called()

    async def test_stop_cancels_running_task_and_clears_handle(
        self,
        pipeline: MagicMock,
        camera_getter: MagicMock,
    ) -> None:
        """After ``stop``, the task handle should be cleared for a clean restart."""
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=0, poll_interval_s=0.01)
        sleeper.start(camera_getter)
        assert sleeper._task is not None
        await sleeper.stop()
        assert sleeper._task is None


class TestRunCancelCleanup:
    """``_run`` stops the encoder on cancel so shutdown doesn't leak ffmpeg."""

    async def test_cancel_stops_encoder_when_running(
        self,
        pipeline: MagicMock,
        camera_getter: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancelling the loop with a running encoder calls ``pipeline.stop``."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        relay_state.mark_relay_connected()
        relay_state.mark_relay_activity()
        pipeline.is_running = True

        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=0, poll_interval_s=10.0)
        sleeper.start(camera_getter)
        # Yield so the task enters ``_run`` and hits the ``await asyncio.sleep``
        # point inside the try block — otherwise a cancel on a pending task
        # raises CancelledError before the except handler runs.
        await asyncio.sleep(0)

        await sleeper.stop()

        pipeline.stop.assert_awaited()

    async def test_cancel_cleanup_tolerates_stop_error(
        self,
        pipeline: MagicMock,
        camera_getter: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the cleanup ``pipeline.stop`` raises, cancel still exits cleanly."""
        monkeypatch.setattr(type(settings), "relay_enabled", property(lambda _self: True))
        relay_state.mark_relay_connected()
        relay_state.mark_relay_activity()
        pipeline.is_running = True
        pipeline.stop.side_effect = RuntimeError("already dead")

        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=0, poll_interval_s=10.0)
        sleeper.start(camera_getter)
        await asyncio.sleep(0)

        # Must not raise — the cleanup error is logged and swallowed.
        await sleeper.stop()

    async def test_cancel_skips_pipeline_stop_when_no_camera(
        self,
        pipeline: MagicMock,
    ) -> None:
        """Cancel with no camera getter skips the pipeline cleanup path."""
        sleeper = PreviewSleeper(pipeline=pipeline, hibernate_after_s=0, poll_interval_s=10.0)
        sleeper.start(lambda: None)
        await asyncio.sleep(0)

        await sleeper.stop()

        pipeline.stop.assert_not_called()


class TestSingleton:
    """``get_preview_sleeper`` returns a process-wide singleton; reset clears it."""

    def test_get_returns_cached_instance(self) -> None:
        """Repeated calls return the same instance."""
        reset_preview_sleeper()
        first = get_preview_sleeper()
        second = get_preview_sleeper()
        assert first is second
        reset_preview_sleeper()

    def test_reset_clears_the_singleton(self) -> None:
        """After reset the next call yields a fresh instance."""
        first = get_preview_sleeper()
        reset_preview_sleeper()
        second = get_preview_sleeper()
        assert first is not second
        reset_preview_sleeper()
