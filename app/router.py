"""Top-level HTTP router composition.

Each feature exposes a ``public_router`` (no app-level auth dep) and a ``router``
(expects the session auth dep to be attached by this aggregator).
"""

from fastapi import APIRouter, Depends

from app.auth.dependencies import verify_request
from app.auth.router import router as auth_router
from app.camera import router as camera
from app.core.frontend_router import router as landing_router
from app.observability import router as system
from app.pairing import router as pairing

router = APIRouter()

for r in [auth_router, landing_router]:
    router.include_router(r, include_in_schema=False)

# Unauthenticated surfaces: HLS preview and setup UI.
router.include_router(camera.public_router)
router.include_router(pairing.public_router)

# Authenticated surfaces: camera controls/captures/stream, local-access
# bootstrap, system telemetry, and metrics.
router.include_router(camera.router, dependencies=[Depends(verify_request)])
router.include_router(pairing.router, dependencies=[Depends(verify_request)])
router.include_router(system.router, dependencies=[Depends(verify_request)])
