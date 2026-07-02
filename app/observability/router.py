"""System HTTP surfaces: Prometheus metrics and device telemetry."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from relab_rpi_cam_models.telemetry import TelemetrySnapshot, ThermalState

from app.observability.telemetry import collect_telemetry

router = APIRouter()

_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _gauge(name: str, help_text: str, value: float, labels: dict[str, str] | None = None) -> list[str]:
    label_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}" if labels else ""
    return [f"# HELP {name} {help_text}", f"# TYPE {name} gauge", f"{name}{label_str} {value}"]


def render_snapshot(snapshot: TelemetrySnapshot) -> str:
    """Render a TelemetrySnapshot as Prometheus text-format exposition."""
    lines: list[str] = (
        _gauge("rpi_cam_cpu_percent", "CPU utilisation as a percentage (0-100).", snapshot.cpu_percent)
        + _gauge("rpi_cam_mem_percent", "Memory utilisation as a percentage (0-100).", snapshot.mem_percent)
        + _gauge("rpi_cam_disk_percent", "Disk utilisation on images volume (0-100).", snapshot.disk_percent)
        + _gauge(
            "rpi_cam_preview_sessions",
            "Number of active LL-HLS preview subscribers on the local MediaMTX.",
            float(snapshot.preview_sessions),
        )
    )
    if snapshot.cpu_temp_c is not None:
        lines += _gauge("rpi_cam_cpu_temp_celsius", "SoC die temperature in Celsius.", snapshot.cpu_temp_c)
    if snapshot.preview_fps is not None:
        lines += _gauge("rpi_cam_preview_fps", "Observed preview encoder frames-per-second.", snapshot.preview_fps)
    lines += [
        "# HELP rpi_cam_thermal_state Thermal governor state (1 for the active band).",
        "# TYPE rpi_cam_thermal_state gauge",
        *(
            f'rpi_cam_thermal_state{{state="{s.value}"}} {1 if snapshot.thermal_state == s else 0}'
            for s in ThermalState
        ),
        "",
    ]
    return "\n".join(lines)


@router.get("/system/telemetry", summary="Get device telemetry snapshot", tags=["system"])
async def get_telemetry() -> TelemetrySnapshot:
    """Return a point-in-time device health snapshot."""
    return await collect_telemetry()


@router.get(
    "/metrics", summary="Prometheus metrics exposition", response_class=PlainTextResponse, include_in_schema=False
)
async def get_metrics() -> PlainTextResponse:
    """Return a Prometheus text-format exposition of the latest device telemetry."""
    return PlainTextResponse(content=render_snapshot(await collect_telemetry()), media_type=_PROM_CONTENT_TYPE)
