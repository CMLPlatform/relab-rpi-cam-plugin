"""Setup and pairing management endpoints."""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.api.routers.local_access import _get_candidate_urls
from app.core.config import DEFAULT_PAIRING_BACKEND_URL, clear_runtime_relay_credentials, settings
from app.core.templates_config import templates
from app.utils.backend_client import notify_self_unpair
from app.utils.pairing import (
    PAIRING_CODE_TTL_SECONDS,
    STATUS_PAIRED,
    _normalize_pairing_backend_base_url,
    delete_relay_credentials,
    get_pairing_state,
    reset_pairing_state,
    run_pairing,
)
from app.utils.relay import run_relay

_STATUS_ERROR = "error"
_PAIRING_BACKEND_REACHABILITY_TIMEOUT = httpx.Timeout(connect=1.5, read=1.5, write=1.5, pool=1.5)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])


def _setup_state_payload() -> dict[str, object]:
    """Return the small state bundle the setup page uses for live refreshes."""
    pairing = get_pairing_state()
    return {
        "relay_enabled": settings.relay_enabled,
        "pairing_status": pairing.status,
        "pairing_code": pairing.code,
        "pairing_error": pairing.error,
        "pairing_expires_at_iso": pairing.expires_at.isoformat() if pairing.expires_at else "",
    }


async def _pairing_backend_reachable() -> bool:
    """Best-effort probe for the configured pairing backend."""
    base_url = settings.pairing_backend_url.rstrip("/")
    if not base_url:
        return False

    normalized_base_url = _normalize_pairing_backend_base_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=_PAIRING_BACKEND_REACHABILITY_TIMEOUT, follow_redirects=True) as client:
            await client.get(normalized_base_url)
    except httpx.HTTPError:
        return False
    return True


@router.get("/setup")
async def setup_page(request: Request) -> HTMLResponse:
    """HTML page showing camera config status and pairing code."""
    base_url = str(settings.base_url).rstrip("/")
    pairing = get_pairing_state()
    pairing_expires_at_iso = pairing.expires_at.isoformat() if pairing.expires_at else ""

    candidate_urls = _get_candidate_urls()
    # Strip scheme and port so the template can compose its own URLs.
    lan_ips = [u.removeprefix("http://").removesuffix(":8018") for u in candidate_urls] or ["<this-ip>"]
    connection_host = lan_ips[0]
    pairing_backend_reachable = settings.relay_enabled or await _pairing_backend_reachable()

    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "pairing": pairing,
            "pairing_expires_at_iso": pairing_expires_at_iso,
            "relay_enabled": settings.relay_enabled,
            "relay_backend_url": settings.relay_backend_url,
            "relay_camera_id": settings.relay_camera_id,
            "pairing_backend_url": settings.pairing_backend_url,
            "default_pairing_backend_url": DEFAULT_PAIRING_BACKEND_URL,
            "base_url": base_url,
            "status_paired": STATUS_PAIRED,
            "status_error": _STATUS_ERROR,
            "pairing_code_ttl_seconds": PAIRING_CODE_TTL_SECONDS,
            "local_mode_enabled": settings.local_mode_enabled,
            "local_api_key": settings.local_api_key,
            "connection_host": connection_host,
            "lan_ips": lan_ips,
            "pairing_backend_reachable": pairing_backend_reachable,
            "setup_state": _setup_state_payload(),
        },
    )


@router.get("/setup/state")
async def setup_state() -> JSONResponse:
    """Return the current setup state for live page refreshes."""
    return JSONResponse(_setup_state_payload())


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


@router.post("/pairing/code/refresh", status_code=204)
async def refresh_pairing_code(request: Request) -> Response:
    """Rotate the active pairing code without deleting credentials.

    Useful when the current code needs to be replaced quickly during setup.
    The current pairing task is cancelled and a fresh one is started so the
    setup page and logs show a new code.
    """
    bg_tasks: set[asyncio.Task[None]] = request.app.state.background_tasks

    async def _on_paired() -> None:
        task = asyncio.create_task(run_relay(), name="ws_relay")
        bg_tasks.add(task)
        logger.info("Re-paired — WebSocket relay restarted")

    async def _do_refresh() -> None:
        await asyncio.sleep(0.1)

        reset_pairing_state()

        for task in asyncio.all_tasks():
            if task.get_name() == "pairing":
                task.cancel()

        if settings.pairing_backend_url and not settings.relay_enabled:
            task = asyncio.create_task(run_pairing(_on_paired), name="pairing")
            bg_tasks.add(task)
            logger.info("Pairing code refreshed — pairing flow restarted")
        else:
            logger.info("Pairing code refreshed — no pairing backend configured")

    task = asyncio.create_task(_do_refresh(), name="pairing_refresh")
    bg_tasks.add(task)
    return Response(status_code=204)
