"""Local-only endpoint for retrieving the direct-connection API key."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.utils.network import is_local_client

router = APIRouter(tags=["local-key"])


@router.get("/local-key", include_in_schema=False)
async def get_local_key(request: Request) -> PlainTextResponse:
    """Return the local API key only to local-network clients."""
    if not is_local_client(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="Local key access is only available from the local network")
    if not settings.local_api_key:
        raise HTTPException(status_code=503, detail="Local API key has not been generated yet")
    return PlainTextResponse(settings.local_api_key)
