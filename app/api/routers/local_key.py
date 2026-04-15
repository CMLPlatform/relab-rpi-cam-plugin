"""Local-only endpoint for retrieving the direct-connection API key."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.core.runtime import get_request_runtime
from app.utils.network import is_local_client

router = APIRouter(tags=["local-key"])


@router.get("/local-key", include_in_schema=False)
async def get_local_key(request: Request) -> PlainTextResponse:
    """Return the local API key only to local-network clients."""
    runtime = get_request_runtime(request)
    if not is_local_client(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="Local key access is only available from the local network")
    if not runtime.runtime_state.local_api_key:
        raise HTTPException(status_code=503, detail="Local API key has not been generated yet")
    return PlainTextResponse(runtime.runtime_state.local_api_key)
