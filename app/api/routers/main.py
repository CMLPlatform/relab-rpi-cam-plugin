"""Main router for the Raspberry Pi camera API."""

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import verify_request
from app.api.routers import auth, camera, hls, images, local_access, local_key, metrics, stream, telemetry
from app.api.routers.frontend import landing

router = APIRouter()

for r in [auth.router, landing.router]:
    router.include_router(r, include_in_schema=False)

# The preview HLS surface and local key endpoint are intentionally
# unauthenticated, but both are constrained to local-network clients.
router.include_router(hls.router)

# /metrics is intentionally unauthenticated — see app/api/routers/metrics.py.
router.include_router(metrics.router)

router.include_router(local_key.router)

for r in [camera.router, images.router, local_access.router, stream.router, telemetry.router]:
    router.include_router(r, dependencies=[Depends(verify_request)])
