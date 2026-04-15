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

import psutil
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
    """Optional mDNS hostname hint if one is available."""


def _get_candidate_urls() -> list[str]:
    """Return all non-loopback IPv4 base URLs for this host.

    Uses psutil.net_if_addrs() to enumerate every network interface so that
    Ethernet LAN addresses (e.g. eth0: 192.168.x.x) are included alongside the
    primary WiFi/outbound address.  The hostname-based approach used by
    socket.getaddrinfo() is unreliable on Linux — it typically resolves to only
    one interface and misses secondary ones.
    """
    ips: set[str] = set()

    # Enumerate every network interface via psutil — the only reliable way to
    # capture all IPs when the Pi has both WiFi and Ethernet active.
    for _iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                ips.add(addr.address)

    return [f"http://{ip}:{_API_PORT}" for ip in sorted(ips)]


def _get_mdns_name() -> str | None:
    """Return an mDNS-style hostname hint, if the current environment provides one."""
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
