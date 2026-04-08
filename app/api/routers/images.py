"""Router for image capture and retrieval."""

import asyncio
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from relab_rpi_cam_models.images import ImageCaptureResponse

from app.api.dependencies.camera_management import CameraManagerDependency
from app.core.config import settings

router = APIRouter(prefix="/images", tags=["images"])


async def _mjpeg_generator(camera_manager: CameraManagerDependency) -> AsyncGenerator[bytes, None]:
    """Yield MJPEG multipart frames, capped at ~20fps."""
    min_interval = 0.05
    while True:
        t0 = time.monotonic()
        try:
            jpeg_bytes = await camera_manager.capture_preview_jpeg()
        except (RuntimeError, asyncio.CancelledError):
            break
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n"
            b"\r\n" + jpeg_bytes + b"\r\n"
        )
        remaining = min_interval - (time.monotonic() - t0)
        if remaining > 0:
            try:
                await asyncio.sleep(remaining)
            except asyncio.CancelledError:
                break


@router.get("/preview")
async def preview_image(camera_manager: CameraManagerDependency) -> Response:
    """Return a low-res JPEG snapshot for viewfinder preview. Does not save to disk."""
    try:
        jpeg_bytes = await camera_manager.capture_preview_jpeg()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return Response(content=jpeg_bytes, media_type="image/jpeg")


@router.get("/mjpeg")
async def mjpeg_stream(camera_manager: CameraManagerDependency) -> StreamingResponse:
    """Stream continuous MJPEG frames for live viewfinder preview."""
    return StreamingResponse(
        _mjpeg_generator(camera_manager),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )


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
async def get_image(image_id: str) -> FileResponse:
    """Retrieve captured image by ID."""
    image_path = settings.image_path / f"{image_id}.jpg"
    if not image_path.exists():
        raise HTTPException(status_code=404, detail=f"Image with ID {image_id} not found")
    return FileResponse(image_path, media_type="image/jpeg")
