"""Router for image capture and retrieval."""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import FileResponse, Response
from relab_rpi_cam_models.images import ImageCaptureResponse

from app.api.dependencies.camera_management import CameraManagerDependency
from app.api.exceptions import ActiveStreamError
from app.core.config import settings

router = APIRouter(prefix="/images", tags=["images"])
logger = logging.getLogger(__name__)


@router.get(
    "/preview",
    summary="Get snapshot preview",
    responses={
        200: {"description": "Single low-resolution JPEG snapshot for polling-based preview."},
        409: {"description": "Preview is unavailable while a YouTube stream is active."},
    },
)
async def preview_image(camera_manager: CameraManagerDependency) -> Response:
    """Return a low-res JPEG snapshot for viewfinder polling without saving it."""
    try:
        jpeg_bytes = await camera_manager.capture_preview_jpeg()
    except ActiveStreamError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RuntimeError as e:
        logger.exception("Preview capture failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    return Response(content=jpeg_bytes, media_type="image/jpeg")


@router.post("", status_code=201, summary="Capture image")
async def capture_image(
    camera_manager: CameraManagerDependency,
) -> ImageCaptureResponse:
    """Capture a full-resolution image and return metadata plus a retrieval URL."""
    try:
        return await camera_manager.capture_jpeg()
    except ActiveStreamError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RuntimeError as e:
        logger.exception("Image capture failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{image_id}", summary="Get captured image")
async def get_image(image_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]) -> FileResponse:
    """Retrieve captured image by ID."""
    image_path = settings.image_path / f"{image_id}.jpg"
    if not image_path.exists():
        raise HTTPException(status_code=404, detail=f"Image with ID {image_id} not found")
    return FileResponse(image_path, media_type="image/jpeg")
