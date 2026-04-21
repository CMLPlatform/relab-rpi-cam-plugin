"""Router for camera status endpoints."""

from fastapi import APIRouter, HTTPException
from relab_rpi_cam_models.camera import CameraStatusView

from app.camera.dependencies import CameraManagerDependency
from app.camera.schemas import (
    CameraControlsPatch,
    CameraControlsView,
    FocusControlRequest,
)
from app.camera.services.manager import CameraControlsNotSupportedError

router = APIRouter(prefix="/camera", tags=["camera"])


@router.get("", summary="Get camera status")
async def get_camera_status(
    camera_manager: CameraManagerDependency,
) -> CameraStatusView:
    """Return the current camera mode and any active stream details."""
    return await camera_manager.get_camera_status()


@router.get(
    "/controls",
    summary="Get camera controls",
    description=(
        "Returns the backend-exposed camera controls along with any "
        "latest observed values. Use this to discover what controls are "
        "available on the current camera."
    ),
)
async def get_camera_controls(
    camera_manager: CameraManagerDependency,
) -> CameraControlsView:
    """Return discoverable camera controls and latest observed values."""
    try:
        return await camera_manager.get_controls()
    except CameraControlsNotSupportedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


@router.patch(
    "/controls",
    summary="Set camera controls",
    description=(
        "Apply backend-native camera controls. Control names are the exact strings reported by /camera/controls."
    ),
)
async def set_camera_controls(
    controls: CameraControlsPatch,
    camera_manager: CameraManagerDependency,
) -> CameraControlsView:
    """Apply backend-native camera controls."""
    try:
        return await camera_manager.set_controls(controls)
    except CameraControlsNotSupportedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put(
    "/focus",
    summary="Set camera focus",
    description="Apply friendly focus controls (continuous, auto, or manual).",
)
async def set_camera_focus(
    focus: FocusControlRequest,
    camera_manager: CameraManagerDependency,
) -> CameraControlsView:
    """Apply friendly focus controls."""
    try:
        return await camera_manager.set_focus(focus)
    except CameraControlsNotSupportedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
