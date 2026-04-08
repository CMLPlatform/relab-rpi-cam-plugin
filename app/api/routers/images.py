"""Router for image capture and retrieval."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import FileResponse, Response
from relab_rpi_cam_models.images import ImageCaptureResponse

from app.api.dependencies.camera_management import CameraManagerDependency
from app.core.config import settings

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/preview")
async def preview_image(camera_manager: CameraManagerDependency) -> Response:
    """Return a low-res JPEG snapshot for viewfinder preview. Does not save to disk."""
    try:
        jpeg_bytes = await camera_manager.capture_preview_jpeg()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return Response(content=jpeg_bytes, media_type="image/jpeg")


@router.post("", status_code=201)
async def capture_image(
    camera_manager: CameraManagerDependency,
) -> ImageCaptureResponse:
    """Capture image and return metadata with URL."""
    try:
        return await camera_manager.capture_jpeg()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{image_id}")
async def get_image(image_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]) -> FileResponse:
    """Retrieve captured image by ID."""
    image_path = settings.image_path / f"{image_id}.jpg"
    if not image_path.exists():
        raise HTTPException(status_code=404, detail=f"Image with ID {image_id} not found")
    return FileResponse(image_path, media_type="image/jpeg")
