"""Main router for the Raspberry Pi camera API."""

from fastapi import APIRouter, Depends

from relab_rpi_cam_plugin.api.dependencies.auth import verify_request
from relab_rpi_cam_plugin.api.routers import camera, images, stream
from relab_rpi_cam_plugin.api.routers.frontend import auth, homepage, stream_viewer

router = APIRouter()

for r in [auth.router, stream_viewer.router, homepage.router]:
    router.include_router(r, include_in_schema=False)

for r in [camera.router, images.router, stream.router]:
    router.include_router(r, dependencies=[Depends(verify_request)])
