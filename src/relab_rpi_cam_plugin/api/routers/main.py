"""Main router for the Raspberry Pi camera API."""

from fastapi import APIRouter, Security

from relab_rpi_cam_plugin.api.dependencies.auth import verify_request
from relab_rpi_cam_plugin.api.routers.camera import router as camera_router
from relab_rpi_cam_plugin.api.routers.images import router as images_router
from relab_rpi_cam_plugin.api.routers.stream import router as stream_router

# Set up API key verification for the main router
router = APIRouter(dependencies=[Security(verify_request)])

router.include_router(camera_router)
router.include_router(images_router)
router.include_router(stream_router)
