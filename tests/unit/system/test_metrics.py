"""Tests for the Prometheus metrics exposition."""

from datetime import UTC, datetime

import pytest
from relab_rpi_cam_models.telemetry import TelemetrySnapshot, ThermalState

from app.system.routers.metrics import render_snapshot
from tests.constants import (
    MET_CPU_10_0,
    MET_CPU_NAME,
    MET_CPU_TEMP_55_5,
    MET_CPU_TEMP_NAME,
    MET_DISK_30_0,
    MET_DISK_NAME,
    MET_MEM_20_0,
    MET_MEM_NAME,
    MET_PREVIEW_FPS_24_5,
    MET_PREVIEW_FPS_NAME,
    MET_PREVIEW_SESSIONS_0_0,
    MET_PREVIEW_SESSIONS_NAME,
)


def _snapshot(**overrides: object) -> TelemetrySnapshot:
    defaults: dict[str, object] = {
        "timestamp": datetime(2026, 4, 14, tzinfo=UTC),
        "cpu_temp_c": 55.5,
        "cpu_percent": 10.0,
        "mem_percent": 20.0,
        "disk_percent": 30.0,
        "preview_fps": None,
        "preview_sessions": 0,
        "thermal_state": ThermalState.NORMAL,
        "current_preview_size": None,
    }
    defaults.update(overrides)
    return TelemetrySnapshot(**defaults)


class TestRenderSnapshot:
    """Tests for the Prometheus text-format renderer."""

    def test_core_gauges_present(self) -> None:
        """Baseline gauges should always appear."""
        text = render_snapshot(_snapshot())
        assert MET_CPU_10_0 in text
        assert MET_MEM_20_0 in text
        assert MET_DISK_30_0 in text
        assert MET_PREVIEW_SESSIONS_0_0 in text
        assert MET_CPU_TEMP_55_5 in text

    def test_help_and_type_lines_emitted(self) -> None:
        """Each gauge must come with HELP and TYPE metadata lines."""
        text = render_snapshot(_snapshot())
        for metric in (
            MET_CPU_NAME,
            MET_MEM_NAME,
            MET_DISK_NAME,
            MET_PREVIEW_SESSIONS_NAME,
            MET_CPU_TEMP_NAME,
            "rpi_cam_thermal_state",
        ):
            assert f"# HELP {metric} " in text
            assert f"# TYPE {metric} gauge" in text

    def test_missing_cpu_temp_omits_metric(self) -> None:
        """cpu_temp_c=None must drop the gauge cleanly."""
        text = render_snapshot(_snapshot(cpu_temp_c=None))

        assert MET_CPU_TEMP_NAME not in text

    def test_missing_preview_fps_omits_metric(self) -> None:
        """preview_fps=None must drop the gauge cleanly."""
        text = render_snapshot(_snapshot(preview_fps=None))
        assert MET_PREVIEW_FPS_NAME not in text

    def test_preview_fps_included_when_present(self) -> None:
        """When preview_fps is set, it should appear as a gauge."""
        text = render_snapshot(_snapshot(preview_fps=24.5))
        assert MET_PREVIEW_FPS_24_5 in text

    @pytest.mark.parametrize(
        ("state", "active_line"),
        [
            (ThermalState.NORMAL, 'rpi_cam_thermal_state{state="normal"} 1'),
            (ThermalState.WARM, 'rpi_cam_thermal_state{state="warm"} 1'),
            (ThermalState.THROTTLE, 'rpi_cam_thermal_state{state="throttle"} 1'),
            (ThermalState.CRITICAL, 'rpi_cam_thermal_state{state="critical"} 1'),
        ],
    )
    def test_thermal_state_labelled_enum(self, state: ThermalState, active_line: str) -> None:
        """Thermal state is a labelled enum: exactly one state has value 1."""
        text = render_snapshot(_snapshot(thermal_state=state))
        assert active_line in text
        for other in ThermalState:
            if other != state:
                assert f'rpi_cam_thermal_state{{state="{other.value}"}} 0' in text

    def test_output_ends_with_newline(self) -> None:
        """Prometheus text format requires a trailing newline."""
        text = render_snapshot(_snapshot())
        assert text.endswith("\n")
