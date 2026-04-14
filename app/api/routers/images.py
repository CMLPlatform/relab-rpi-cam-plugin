"""Router for image capture.

The Pi no longer exposes captured bytes over HTTP. Captures are pushed directly
from the Pi to the backend's upload endpoint (``backend_client.upload_image``),
with a local queue fallback on push failure. The old ``GET /images/{id}``
retrieval endpoint has been removed — bytes live in exactly one place.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response
from relab_rpi_cam_models.images import ImageCaptureResponse

from app.api.dependencies.camera_management import CameraManagerDependency
from app.api.exceptions import ActiveStreamError

router = APIRouter(prefix="/images", tags=["images"])
logger = logging.getLogger(__name__)


@router.get(
    "/preview",
    summary="Get snapshot preview",
    responses={
        200: {"description": "Single low-resolution JPEG snapshot for polling-based preview."},
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
    upload_metadata: Annotated[
        dict[str, Any] | None,
        Body(
            description=(
                "Opaque metadata forwarded to the backend upload endpoint. Typically includes "
                "`product_id` and `description` for the parent association, but the Pi treats "
                "this dict as a pass-through so non-standard backends can add their own fields."
            )
        ),
    ] = None,
) -> ImageCaptureResponse:
    """Capture a full-resolution image, push it to the backend, and return the result."""
    try:
        return await camera_manager.capture_jpeg(upload_metadata=upload_metadata)
    except ActiveStreamError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RuntimeError as e:
        logger.exception("Image capture failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
