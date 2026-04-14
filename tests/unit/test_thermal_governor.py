"""Tests for the hysteresis-based thermal governor."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from relab_rpi_cam_models.telemetry import TelemetrySnapshot, ThermalState

from app.utils import thermal_governor as thermal_governor_mod
from app.utils.thermal_governor import ThermalGovernor


def _snapshot(cpu_temp_c: float | None) -> TelemetrySnapshot:
    state = ThermalState.NORMAL
    if cpu_temp_c is not None:
        if cpu_temp_c >= 80:
            state = ThermalState.CRITICAL
        elif cpu_temp_c >= 75:
            state = ThermalState.THROTTLE
        elif cpu_temp_c >= 60:
            state = ThermalState.WARM
    return TelemetrySnapshot(
        timestamp=datetime.now(UTC),
        cpu_temp_c=cpu_temp_c,
        cpu_percent=0.0,
        mem_percent=0.0,
        disk_percent=0.0,
        preview_fps=None,
        preview_sessions=0,
        thermal_state=state,
        current_preview_size=None,
    )


@pytest.fixture
def pipeline() -> MagicMock:
    """A mock pipeline with an async set_bitrate method."""
    p = MagicMock()
    p.set_bitrate = AsyncMock()
    return p


@pytest.fixture
def governor(pipeline: MagicMock) -> ThermalGovernor:
    """A ThermalGovernor with a mocked pipeline and no sustain time."""
    gov = ThermalGovernor(
        pipeline,
        sustain_drop_s=0.0,
        sustain_restore_s=0.0,
    )
    gov._camera_getter = MagicMock
    return gov


class TestTick:
    """Single-tick behaviour across the hysteresis bands."""

    async def test_cool_temp_does_nothing(
        self,
        governor: ThermalGovernor,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Temperatures in the NORMAL band should not toggle anything."""
        monkeypatch.setattr(thermal_governor_mod, "collect_telemetry", AsyncMock(return_value=_snapshot(45.0)))

        await governor._tick()

        assert governor.is_throttled is False
        pipeline.set_bitrate.assert_not_called()

    async def test_hot_sustained_drops_bitrate(
        self,
        governor: ThermalGovernor,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sustained temperature over the drop threshold should throttle the encoder."""
        monkeypatch.setattr(thermal_governor_mod, "collect_telemetry", AsyncMock(return_value=_snapshot(85.0)))

        # Need two ticks: first records the timestamp, second passes sustain_drop=0 and fires.
        await governor._tick()
        await governor._tick()

        assert governor.is_throttled is True
        pipeline.set_bitrate.assert_awaited_once()
        assert pipeline.set_bitrate.await_args.args[1] == 200_000

    async def test_cool_after_throttle_restores_bitrate(
        self,
        governor: ThermalGovernor,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After throttling, a sustained cool reading should restore the high bitrate."""
        # Throttle first.
        monkeypatch.setattr(thermal_governor_mod, "collect_telemetry", AsyncMock(return_value=_snapshot(85.0)))
        await governor._tick()
        await governor._tick()
        assert governor.is_throttled is True
        pipeline.set_bitrate.reset_mock()

        # Then cool below the restore threshold.
        monkeypatch.setattr(thermal_governor_mod, "collect_telemetry", AsyncMock(return_value=_snapshot(65.0)))
        await governor._tick()
        await governor._tick()

        assert governor.is_throttled is False
        pipeline.set_bitrate.assert_awaited_once()
        assert pipeline.set_bitrate.await_args.args[1] == 500_000

    async def test_hysteresis_band_does_not_toggle(
        self,
        governor: ThermalGovernor,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Temperatures between restore and drop should leave the state alone."""
        monkeypatch.setattr(thermal_governor_mod, "collect_telemetry", AsyncMock(return_value=_snapshot(75.0)))

        await governor._tick()
        await governor._tick()

        assert governor.is_throttled is False
        pipeline.set_bitrate.assert_not_called()

    async def test_missing_temperature_is_safe(
        self,
        governor: ThermalGovernor,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """None cpu_temp_c (e.g. dev host) must not crash the tick."""
        monkeypatch.setattr(thermal_governor_mod, "collect_telemetry", AsyncMock(return_value=_snapshot(None)))

        await governor._tick()

        assert governor.is_throttled is False
        pipeline.set_bitrate.assert_not_called()


class TestLifecycle:
    """Background task start/stop should be clean and cancellable."""

    async def test_start_then_stop_does_not_raise(
        self,
        pipeline: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The governor must start and stop cleanly even when polling is fast."""
        monkeypatch.setattr(thermal_governor_mod, "collect_telemetry", AsyncMock(return_value=_snapshot(50.0)))

        gov = ThermalGovernor(pipeline, poll_interval_s=0.01, sustain_drop_s=0.0, sustain_restore_s=0.0)
        gov.start(camera_getter=MagicMock)

        await asyncio.sleep(0.05)
        await gov.stop()
