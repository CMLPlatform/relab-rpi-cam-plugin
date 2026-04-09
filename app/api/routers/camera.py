"""Router for camera status endpoints."""

from fastapi import APIRouter
from relab_rpi_cam_models.camera import CameraStatusView

from app.api.dependencies.camera_management import CameraManagerDependency

router = APIRouter(prefix="/camera", tags=["camera"])


@router.get("", summary="Get camera status")
async def get_camera_status(
    camera_manager: CameraManagerDependency,
) -> CameraStatusView:
    """Return the current camera mode and any active stream details."""
    return await camera_manager.get_status()
