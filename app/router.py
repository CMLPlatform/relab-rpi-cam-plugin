"""Top-level HTTP router composition.

Each feature exposes a ``public_router`` (no app-level auth dep) and a ``router``
(expects the session auth dep to be attached by this aggregator).
"""

from fastapi import APIRouter, Depends

from app.auth.dependencies import verify_request
from app.auth.router import router as auth_router
from app.camera import router as camera
from app.frontend.router import router as landing_router
from app.pairing import router as pairing
from app.system import router as system

router = APIRouter()

for r in [auth_router, landing_router]:
    router.include_router(r, include_in_schema=False)

# Unauthenticated surfaces: HLS preview, setup UI, local key, metrics.
router.include_router(camera.public_router)
router.include_router(pairing.public_router)
router.include_router(system.public_router)

# Authenticated surfaces: camera controls/captures/stream, local-access
# bootstrap, system telemetry.
router.include_router(camera.router, dependencies=[Depends(verify_request)])
router.include_router(pairing.router, dependencies=[Depends(verify_request)])
router.include_router(system.router, dependencies=[Depends(verify_request)])
