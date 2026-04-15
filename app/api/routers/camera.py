"""Router for camera status endpoints."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from relab_rpi_cam_models.camera import CameraStatusView

from app.api.dependencies.camera_management import CameraManagerDependency
from app.api.exceptions import ActiveStreamError
from app.api.schemas.camera_controls import (
    CameraControlsCapabilities,
    CameraControlsPatch,
    CameraControlsView,
    FocusControlRequest,
)
from app.api.services.camera_manager import CameraControlsNotSupportedError

router = APIRouter(prefix="/camera", tags=["camera"])


@router.get("", summary="Get camera status")
async def get_camera_status(
    camera_manager: CameraManagerDependency,
) -> CameraStatusView:
    """Return the current camera mode and any active stream details."""
    return await camera_manager.get_status()


@router.get(
    "/snapshot",
    summary="Get low-res JPEG snapshot for viewfinder preview",
    description=(
        "Fetch a single low-resolution preview frame from the camera without saving it. "
        "Returns 409 while a YouTube stream is active."
    ),
)
async def get_camera_snapshot(camera_manager: CameraManagerDependency) -> Response:
    """Return a low-resolution JPEG snapshot from the camera."""
    try:
        snapshot = await camera_manager.capture_snapshot_jpeg()
    except ActiveStreamError as exc:
        raise HTTPException(status_code=409, detail="Preview unavailable while a stream is active.") from exc
    return Response(content=snapshot, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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


@router.get("/controls/capabilities", summary="Get camera control capabilities")
async def get_camera_control_capabilities(
    camera_manager: CameraManagerDependency,
) -> CameraControlsCapabilities:
    """Return a UI-friendly list of supported camera controls."""
    try:
        return await camera_manager.get_controls_capabilities()
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
