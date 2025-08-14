"""Main router for the Raspberry Pi camera API."""

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import verify_request
from app.api.routers import camera, images, stream
from app.api.routers.frontend import auth, homepage, stream_viewer

router = APIRouter()

for r in [auth.router, stream_viewer.router, homepage.router]:
    router.include_router(r, include_in_schema=False)

for r in [camera.router, images.router, stream.router]:
    router.include_router(r, dependencies=[Depends(verify_request)])
