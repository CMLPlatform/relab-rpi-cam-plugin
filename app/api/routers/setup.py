"""Setup and pairing management endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.core.config import clear_runtime_relay_credentials, settings
from app.core.templates_config import templates
from app.utils.backend_client import notify_self_unpair
from app.utils.pairing import (
    PAIRING_CODE_TTL_SECONDS,
    STATUS_PAIRED,
    delete_relay_credentials,
    get_pairing_state,
    run_pairing,
)
from app.utils.relay import run_relay

_STATUS_ERROR = "error"

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])


@router.get("/setup")
async def setup_page(request: Request) -> HTMLResponse:
    """HTML page showing camera config status and pairing code."""
    base_url = str(settings.base_url).rstrip("/")
    pairing = get_pairing_state()
    pairing_expires_at_iso = pairing.expires_at.isoformat() if pairing.expires_at else ""

    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "pairing": pairing,
            "pairing_expires_at_iso": pairing_expires_at_iso,
            "relay_enabled": settings.relay_enabled,
            "relay_backend_url": settings.relay_backend_url,
            "relay_camera_id": settings.relay_camera_id,
            "base_url": base_url,
            "status_paired": STATUS_PAIRED,
            "status_error": _STATUS_ERROR,
            "pairing_code_ttl_seconds": PAIRING_CODE_TTL_SECONDS,
            "local_mode_enabled": settings.local_mode_enabled,
            "local_api_key": settings.local_api_key,
        },
    )


@router.delete("/pairing/credentials", status_code=204)
async def unpair(request: Request) -> Response:
    """Clear relay credentials and restart the pairing flow.

    Returns 204 immediately so the response can propagate back through the
    relay WebSocket before the relay task is cancelled. The actual reset runs
    100 ms later in a background task.

    Called two ways:
    - By the ReLab backend through the relay when a camera is deleted.
    - By the local browser UI "Unpair" button on the setup page.
    """
    bg_tasks: set[asyncio.Task[None]] = request.app.state.background_tasks

    async def _on_repaired() -> None:
        task = asyncio.create_task(run_relay(), name="ws_relay")
        bg_tasks.add(task)
        logger.info("Re-paired — WebSocket relay restarted")

    async def _do_reset() -> None:
        # Small delay so the 204 response travels back through the relay WS
        # before the relay task receives its CancelledError.
        await asyncio.sleep(0.1)

        # Notify the backend to delete this camera's registration. Best-effort:
        # errors are logged but never block the local unpair from completing.
        await notify_self_unpair()

        for task in asyncio.all_tasks():
            if task.get_name() in ("ws_relay", "pairing"):
                task.cancel()

        delete_relay_credentials()
        clear_runtime_relay_credentials()

        if settings.pairing_backend_url:
            task = asyncio.create_task(run_pairing(_on_repaired), name="pairing")
            bg_tasks.add(task)
            logger.info("Unpairing complete — pairing flow restarted")
        else:
            logger.info("Unpairing complete — no pairing backend configured, staying idle")

    task = asyncio.create_task(_do_reset(), name="pairing_reset")
    bg_tasks.add(task)
    return Response(status_code=204)
