"""Compose the camera feature's HTTP routers.

``public_router`` is unauthenticated (HLS preview is constrained to local-network
clients at a different layer). ``router`` carries the rest and expects the parent
aggregator to attach the session auth dependency.
"""

from fastapi import APIRouter

from app.camera.routers import captures, controls, hls, stream

public_router = APIRouter()
public_router.include_router(hls.router)

router = APIRouter()
router.include_router(controls.router)
router.include_router(captures.router)
router.include_router(stream.router)
