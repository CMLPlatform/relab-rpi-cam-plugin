"""Prometheus metrics exposition for optional Alloy/Prometheus scraping.

Unlike the authenticated `/telemetry` JSON endpoint, `/metrics` is deliberately
unauthenticated: it's designed to be scraped by a colocated Alloy or Prometheus
instance on a trusted network (the compose-internal docker network). The values
it exposes are benign system stats (CPU%, memory%, disk%, SoC temperature,
preview session count) — nothing sensitive.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from relab_rpi_cam_models.telemetry import TelemetrySnapshot, ThermalState

from app.utils.telemetry import collect_telemetry

router = APIRouter(tags=["metrics"])

_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _gauge(name: str, help_text: str, value: float, labels: dict[str, str] | None = None) -> list[str]:
    label_str = ""
    if labels:
        label_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} gauge",
        f"{name}{label_str} {value}",
    ]


def render_snapshot(snapshot: TelemetrySnapshot) -> str:
    """Render a TelemetrySnapshot as Prometheus text-format exposition."""
    lines: list[str] = []
    lines += _gauge("rpi_cam_cpu_percent", "CPU utilisation as a percentage (0-100).", snapshot.cpu_percent)
    lines += _gauge("rpi_cam_mem_percent", "Memory utilisation as a percentage (0-100).", snapshot.mem_percent)
    lines += _gauge("rpi_cam_disk_percent", "Disk utilisation on images volume (0-100).", snapshot.disk_percent)
    lines += _gauge(
        "rpi_cam_preview_sessions",
        "Number of active WHEP preview subscribers on the local MediaMTX.",
        float(snapshot.preview_sessions),
    )
    if snapshot.cpu_temp_c is not None:
        lines += _gauge("rpi_cam_cpu_temp_celsius", "SoC die temperature in Celsius.", snapshot.cpu_temp_c)
    if snapshot.preview_fps is not None:
        lines += _gauge(
            "rpi_cam_preview_fps",
            "Observed preview encoder frames-per-second.",
            snapshot.preview_fps,
        )

    # Thermal state as a labelled enum series (standard Prometheus idiom for enums).
    lines += ["# HELP rpi_cam_thermal_state Thermal governor state (1 for the active band).", "# TYPE rpi_cam_thermal_state gauge"]
    for state in ThermalState:
        active = 1 if snapshot.thermal_state == state else 0
        lines.append(f'rpi_cam_thermal_state{{state="{state.value}"}} {active}')

    lines.append("")  # trailing newline
    return "\n".join(lines)


@router.get(
    "/metrics",
    summary="Prometheus metrics exposition",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def get_metrics() -> PlainTextResponse:
    """Return a Prometheus text-format exposition of the latest device telemetry."""
    snapshot = await collect_telemetry()
    return PlainTextResponse(content=render_snapshot(snapshot), media_type=_PROM_CONTENT_TYPE)
