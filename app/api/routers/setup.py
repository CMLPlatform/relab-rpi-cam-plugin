"""Setup and pairing management endpoints."""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.api.routers.local_access import _get_candidate_urls
from app.core.config import DEFAULT_PAIRING_BACKEND_URL, clear_runtime_relay_credentials, settings
from app.core.runtime import get_request_runtime
from app.core.templates_config import templates
from app.utils.backend_client import notify_self_unpair
from app.utils.logging import build_log_extra
from app.utils.pairing import (
    PAIRING_CODE_TTL_SECONDS,
    STATUS_PAIRED,
    _normalize_pairing_backend_base_url,
    delete_relay_credentials,
)

_STATUS_ERROR = "error"
_PAIRING_BACKEND_REACHABILITY_TIMEOUT = httpx.Timeout(connect=1.5, read=1.5, write=1.5, pool=1.5)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])


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
    runtime = get_request_runtime(request)
    base_url = str(settings.base_url).rstrip("/")
    pairing = runtime.pairing_service.get_state()
    pairing_expires_at_iso = pairing.expires_at.isoformat() if pairing.expires_at else ""

    candidate_urls = _get_candidate_urls()
    # Strip scheme and port so the template can compose its own URLs.
    lan_ips = [u.removeprefix("http://").removesuffix(":8018") for u in candidate_urls] or ["<this-ip>"]
    connection_host = lan_ips[0]
    pairing_backend_reachable = runtime.runtime_state.relay_enabled or await _pairing_backend_reachable()

    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "pairing": pairing,
            "pairing_expires_at_iso": pairing_expires_at_iso,
            "relay_enabled": runtime.runtime_state.relay_enabled,
            "relay_backend_url": runtime.runtime_state.relay_backend_url,
            "relay_camera_id": runtime.runtime_state.relay_camera_id,
            "pairing_backend_url": settings.pairing_backend_url,
            "default_pairing_backend_url": DEFAULT_PAIRING_BACKEND_URL,
            "base_url": base_url,
            "status_paired": STATUS_PAIRED,
            "status_error": _STATUS_ERROR,
            "pairing_code_ttl_seconds": PAIRING_CODE_TTL_SECONDS,
            "local_mode_enabled": settings.local_mode_enabled,
            "local_api_key": runtime.runtime_state.local_api_key,
            "connection_host": connection_host,
            "lan_ips": lan_ips,
            "pairing_backend_reachable": pairing_backend_reachable,
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
    runtime = get_request_runtime(request)

    async def _on_repaired() -> None:
        runtime.create_task(runtime.relay_service.run_forever(), name="ws_relay")
        logger.info("Re-paired — WebSocket relay restarted", extra=build_log_extra())

    async def _do_reset() -> None:
        # Small delay so the 204 response travels back through the relay WS
        # before the relay task receives its CancelledError.
        await asyncio.sleep(0.1)

        # Notify the backend to delete this camera's registration. Best-effort:
        # errors are logged but never block the local unpair from completing.
        await notify_self_unpair()

        runtime.cancel_tasks({"ws_relay", "pairing"})

        delete_relay_credentials()
        clear_runtime_relay_credentials(runtime.runtime_state)
        runtime.pairing_service.reset_state()

        if settings.pairing_backend_url:
            runtime.create_task(runtime.pairing_service.run_forever(_on_repaired), name="pairing")
            logger.info("Unpairing complete — pairing flow restarted", extra=build_log_extra())
        else:
            logger.info(
                "Unpairing complete — no pairing backend configured, staying idle",
                extra=build_log_extra(),
            )

    runtime.create_task(_do_reset(), name="pairing_reset")
    return Response(status_code=204)


@router.post("/pairing/code/refresh", status_code=204)
async def refresh_pairing_code(request: Request) -> Response:
    """Rotate the active pairing code without deleting credentials.

    Useful when the current code needs to be replaced quickly during setup.
    The current pairing task is cancelled and a fresh one is started so the
    setup page and logs show a new code.
    """
    runtime = get_request_runtime(request)

    async def _on_paired() -> None:
        runtime.create_task(runtime.relay_service.run_forever(), name="ws_relay")
        logger.info("Re-paired — WebSocket relay restarted", extra=build_log_extra())

    async def _do_refresh() -> None:
        await asyncio.sleep(0.1)

        runtime.pairing_service.reset_state()

        runtime.cancel_tasks({"pairing"})

        if settings.pairing_backend_url and not runtime.runtime_state.relay_enabled:
            runtime.create_task(runtime.pairing_service.run_forever(_on_paired), name="pairing")
            logger.info("Pairing code refreshed — pairing flow restarted", extra=build_log_extra())
        else:
            logger.info("Pairing code refreshed — no pairing backend configured", extra=build_log_extra())

    runtime.create_task(_do_refresh(), name="pairing_refresh")
    return Response(status_code=204)
