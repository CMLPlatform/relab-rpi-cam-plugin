"""Router for device telemetry."""

from fastapi import APIRouter
from relab_rpi_cam_models.telemetry import TelemetrySnapshot

from app.utils.telemetry import collect_telemetry

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/telemetry", summary="Get device telemetry snapshot")
async def get_telemetry() -> TelemetrySnapshot:
    """Return a point-in-time device health snapshot."""
    return await collect_telemetry()
