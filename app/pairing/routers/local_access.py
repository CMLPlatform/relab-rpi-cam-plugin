"""Local direct-connection bootstrap endpoint.

Returns the local API key and candidate IP:port URLs so the frontend can
auto-configure direct (Ethernet / USB-C) access without manual copy-paste.
This endpoint is called by the backend via the WebSocket relay using the
relay's own authenticated connection — the response travels back to the
frontend through the relay's /cameras/{id}/local-access proxy.
"""

from __future__ import annotations

import logging
import socket

from fastapi import APIRouter, Request, Response

from app.core.headers import NO_STORE_HEADERS
from app.core.runtime import get_request_runtime
from app.utils.network import find_lan_ips
from relab_rpi_cam_models import LocalAccessInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

_API_PORT = 8018


def get_candidate_urls() -> list[str]:
    """Return all non-loopback IPv4 base URLs for this host."""
    return [f"http://{ip}:{_API_PORT}" for ip in find_lan_ips()]


def _get_mdns_name() -> str | None:
    """Return an mDNS-style hostname hint, if the current environment provides one."""
    try:
        return f"{socket.gethostname()}.local"
    except OSError:
        return None


@router.get("/local-access", summary="Get local direct-connection info")
async def get_local_access_info(request: Request, response: Response) -> LocalAccessInfo:
    """Return local API key and candidate IP URLs for direct (Ethernet/USB-C) access.

    Called by the backend through the WebSocket relay when the user opens the
    camera detail screen.  The response is forwarded to the authenticated frontend
    user so the app can auto-configure local mode without manual key copying.
    """
    response.headers.update(NO_STORE_HEADERS)
    runtime = get_request_runtime(request)
    return LocalAccessInfo.model_validate(
        {
            "local_api_key": runtime.runtime_state.local_api_key,
            "candidate_urls": get_candidate_urls(),
            "mdns_name": _get_mdns_name(),
        }
    )
