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

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["local-access"])

_API_PORT = 8018
_MEDIA_PORT = 8888


class LocalAccessInfo(BaseModel):
    """Payload returned by GET /local-access-info."""

    local_api_key: str
    """The locally-generated API key accepted by direct connections to this Pi."""

    candidate_urls: list[str]
    """FastAPI base URLs to probe, e.g. ["http://192.168.1.100:8018"].
    The frontend tries each in parallel; the first that responds wins."""

    mdns_name: str | None
    """mDNS hostname if resolvable, e.g. "relab-rpi-cam-mypi.local"."""


def _get_candidate_urls() -> list[str]:
    """Return all non-loopback IPv4 base URLs for this host."""
    ips: set[str] = set()

    # Primary outbound interface — the IP the system would use to reach the internet.
    # Works even on air-gapped setups because connect() doesn't actually send packets.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass

    # All IPs bound to this hostname
    try:
        for _fam, _typ, _proto, _name, addr in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET
        ):
            ip: str = addr[0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass

    return [f"http://{ip}:{_API_PORT}" for ip in sorted(ips)]


def _get_mdns_name() -> str | None:
    try:
        return f"{socket.gethostname()}.local"
    except OSError:
        return None


@router.get("/local-access-info", summary="Get local direct-connection info")
async def get_local_access_info() -> LocalAccessInfo:
    """Return local API key and candidate IP URLs for direct (Ethernet/USB-C) access.

    Called by the backend through the WebSocket relay when the user opens the
    camera detail screen.  The response is forwarded to the authenticated frontend
    user so the app can auto-configure local mode without manual key copying.
    """
    return LocalAccessInfo(
        local_api_key=settings.local_api_key,
        candidate_urls=_get_candidate_urls(),
        mdns_name=_get_mdns_name(),
    )
