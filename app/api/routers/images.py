"""Router for image capture.

The Pi no longer exposes captured bytes over HTTP. Captures are pushed directly
from the Pi to the backend's upload endpoint (``backend_client.upload_image``),
with a local queue fallback on push failure. The Pi exposes a command-style
capture endpoint only; bytes live in exactly one place.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from relab_rpi_cam_models.images import ImageCaptureResponse

from app.api.dependencies.camera_management import CameraManagerDependency
from app.api.exceptions import ActiveStreamError
from app.utils.logging import build_log_extra

router = APIRouter(prefix="/captures", tags=["captures"])
logger = logging.getLogger(__name__)


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
        logger.exception("Image capture failed", extra=build_log_extra(stream_mode=camera_manager.stream.mode))
        raise HTTPException(status_code=500, detail=str(e)) from e
