"""Compose the system feature's HTTP routers.

``router`` carries both ``/metrics`` and ``/telemetry`` so the parent
aggregator attaches the normal API-key/session auth dependency to both.
"""

from fastapi import APIRouter

from app.system.routers import metrics, telemetry

public_router = APIRouter()

router = APIRouter()
router.include_router(metrics.router)
router.include_router(telemetry.router)
