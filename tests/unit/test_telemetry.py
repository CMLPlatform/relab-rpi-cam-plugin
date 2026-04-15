"""Tests for the device telemetry utility."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from relab_rpi_cam_models.telemetry import ThermalState

from app.utils import telemetry as telemetry_mod


class TestClassifyThermal:
    """Tests for _classify_thermal band mapping."""

    @pytest.mark.parametrize(
        ("temp", "expected"),
        [
            (None, ThermalState.NORMAL),
            (40.0, ThermalState.NORMAL),
            (59.9, ThermalState.NORMAL),
            (60.0, ThermalState.WARM),
            (74.9, ThermalState.WARM),
            (75.0, ThermalState.THROTTLE),
            (79.9, ThermalState.THROTTLE),
            (80.0, ThermalState.CRITICAL),
            (95.0, ThermalState.CRITICAL),
        ],
    )
    def test_bands(self, temp: float | None, expected: ThermalState) -> None:
        """Temperature bands should map to the expected thermal state."""
        assert telemetry_mod._classify_thermal(temp) == expected


class TestReadCpuTempC:
    """Tests for sysfs-backed CPU temperature reading."""

    def test_missing_node_returns_none(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """A missing thermal_zone0/temp node must not raise."""
        monkeypatch.setattr(telemetry_mod, "_THERMAL_ZONE", tmp_path / "absent" / "temp")
        assert telemetry_mod._read_cpu_temp_c() is None

    def test_valid_reading_parsed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """A numeric millicelsius reading should be converted to Celsius."""
        fake = tmp_path / "temp"
        fake.write_text("54321\n")
        monkeypatch.setattr(telemetry_mod, "_THERMAL_ZONE", fake)
        assert telemetry_mod._read_cpu_temp_c() == pytest.approx(54.321)

    def test_garbage_reading_returns_none(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Non-numeric content must not propagate exceptions."""
        fake = tmp_path / "temp"
        fake.write_text("nonsense")
        monkeypatch.setattr(telemetry_mod, "_THERMAL_ZONE", fake)
        assert telemetry_mod._read_cpu_temp_c() is None


class TestCollectTelemetry:
    """End-to-end tests for collect_telemetry with psutil mocked."""

    async def test_snapshot_fields_populated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A collected snapshot should contain all required fields."""
        monkeypatch.setattr(telemetry_mod, "_read_cpu_temp_c", lambda: 66.5)
        monkeypatch.setattr(telemetry_mod.psutil, "cpu_percent", MagicMock(return_value=12.5))
        monkeypatch.setattr(
            telemetry_mod.psutil,
            "virtual_memory",
            lambda: MagicMock(percent=42.0),
        )
        monkeypatch.setattr(
            telemetry_mod.shutil,
            "disk_usage",
            lambda _path: MagicMock(used=500, total=1000),
        )

        snapshot = await telemetry_mod.collect_telemetry()

        assert snapshot.cpu_temp_c == pytest.approx(66.5)
        assert snapshot.cpu_percent == pytest.approx(12.5)
        assert snapshot.mem_percent == pytest.approx(42.0)
        assert snapshot.disk_percent == pytest.approx(50.0)
        assert snapshot.thermal_state == ThermalState.WARM
        assert snapshot.preview_fps is None
        assert snapshot.preview_sessions == 0
        assert snapshot.current_preview_size is None
        assert snapshot.timestamp.tzinfo is not None

    async def test_missing_temp_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the thermal node is unavailable the snapshot still succeeds."""
        monkeypatch.setattr(telemetry_mod, "_read_cpu_temp_c", lambda: None)
        monkeypatch.setattr(telemetry_mod.psutil, "cpu_percent", MagicMock(return_value=0.0))
        monkeypatch.setattr(telemetry_mod.psutil, "virtual_memory", lambda: MagicMock(percent=0.0))
        monkeypatch.setattr(
            telemetry_mod.shutil,
            "disk_usage",
            lambda _path: MagicMock(used=0, total=1),
        )

        snapshot = await telemetry_mod.collect_telemetry()

        assert snapshot.cpu_temp_c is None
        assert snapshot.thermal_state == ThermalState.NORMAL
