"""Device telemetry contracts for the Raspberry Pi camera plugin."""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field


class ThermalState(StrEnum):
    """Coarse thermal classification used by the thermal governor."""

    NORMAL = "normal"
    WARM = "warm"
    THROTTLE = "throttle"
    CRITICAL = "critical"


class TelemetrySnapshot(BaseModel):
    """Point-in-time device health sample emitted by the Pi plugin."""

    timestamp: AwareDatetime = Field(description="When this snapshot was collected.")
    cpu_temp_c: float | None = Field(
        default=None,
        description="CPU die temperature in Celsius; None if the sysfs reading is unavailable.",
    )
    cpu_percent: float = Field(description="CPU utilisation as a percentage (0-100).")
    mem_percent: float = Field(description="Memory utilisation as a percentage (0-100).")
    disk_percent: float = Field(description="Disk utilisation on the images volume as a percentage (0-100).")
    preview_fps: float | None = Field(
        default=None,
        description="Observed preview encoder frame rate, or None if no preview pipeline is active.",
    )
    preview_sessions: int = Field(
        default=0,
        description="Number of active WHEP preview subscribers on the local MediaMTX instance.",
    )
    thermal_state: ThermalState = Field(description="Thermal governor classification for this snapshot.")
    current_preview_size: tuple[int, int] | None = Field(
        default=None,
        description="Current lores-stream size (width, height); None when no preview pipeline is configured.",
    )
