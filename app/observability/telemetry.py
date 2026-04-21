"""Device telemetry collection for the Raspberry Pi camera plugin."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import psutil
from relab_rpi_cam_models.telemetry import TelemetrySnapshot, ThermalState

from app.core.settings import settings

logger = logging.getLogger(__name__)

# Raspberry Pi SoC temperature sysfs path. Both Pi 4 and Pi 5 expose zone0.
_THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")


def _read_cpu_temp_c() -> float | None:
    """Return the SoC temperature in Celsius, or None if the sysfs node is unavailable."""
    try:
        raw = _THERMAL_ZONE.read_text().strip()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        logger.debug("Thermal sysfs read failed: %s", exc)
        return None
    try:
        return int(raw) / 1000.0
    except ValueError:
        logger.debug("Thermal sysfs returned non-numeric value: %r", raw)
        return None


def _classify_thermal(cpu_temp_c: float | None) -> ThermalState:
    """Map a CPU temperature to a coarse thermal band."""
    if cpu_temp_c is None:
        return ThermalState.NORMAL
    if cpu_temp_c >= 80:
        return ThermalState.CRITICAL
    if cpu_temp_c >= 75:
        return ThermalState.THROTTLE
    if cpu_temp_c >= 60:
        return ThermalState.WARM
    return ThermalState.NORMAL


async def collect_telemetry() -> TelemetrySnapshot:
    """Capture a point-in-time device health snapshot."""
    cpu_temp_c = await asyncio.to_thread(_read_cpu_temp_c)
    # Request a non-per-CPU percent to ensure a single float is returned
    cpu_percent = cast("float", await asyncio.to_thread(psutil.cpu_percent, interval=None, percpu=False))
    mem = await asyncio.to_thread(psutil.virtual_memory)
    disk_usage = await asyncio.to_thread(shutil.disk_usage, settings.image_path)

    return TelemetrySnapshot(
        timestamp=datetime.now(UTC),
        cpu_temp_c=cpu_temp_c,
        cpu_percent=float(cpu_percent),
        mem_percent=float(mem.percent),
        disk_percent=round(disk_usage.used / disk_usage.total * 100, 2),
        preview_fps=None,
        preview_sessions=0,
        thermal_state=_classify_thermal(cpu_temp_c),
        current_preview_size=None,
    )
