"""Compose the pairing feature's HTTP routers.

``public_router`` covers endpoints intentionally unauthenticated at the app
level (setup UI, local-key fetch). ``router`` carries the authenticated
local-access bootstrap.
"""

from fastapi import APIRouter

from app.pairing.routers import local_access, local_key, setup

public_router = APIRouter()
public_router.include_router(setup.router, include_in_schema=False)
public_router.include_router(local_key.router)

router = APIRouter()
router.include_router(local_access.router)
