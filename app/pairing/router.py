"""Compose the pairing feature's HTTP routers.

``public_router`` covers endpoints intentionally unauthenticated at the app
level (setup UI). ``router`` carries authenticated local-access bootstrap
endpoints.
"""

from fastapi import APIRouter

from app.pairing.routers import local_access, local_key, setup

public_router = APIRouter()
public_router.include_router(setup.public_router, include_in_schema=False)

router = APIRouter()
router.include_router(setup.router)
router.include_router(local_access.router)
router.include_router(local_key.router)
