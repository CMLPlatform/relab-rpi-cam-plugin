"""Compose the system feature's HTTP routers.

``public_router`` carries ``/metrics`` (intentionally unauthenticated — see
the module docstring). ``router`` carries ``/telemetry`` which the parent
aggregator attaches the session auth dependency to.
"""

from fastapi import APIRouter

from app.system.routers import metrics, telemetry

public_router = APIRouter()
public_router.include_router(metrics.router)

router = APIRouter()
router.include_router(telemetry.router)
