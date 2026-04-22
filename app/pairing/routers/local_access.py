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
from fastapi import APIRouter, Request

from app.core.runtime import get_request_runtime
from relab_rpi_cam_models import LocalAccessInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

_API_PORT = 8018
_DOCKERISH_INTERFACE_PREFIXES = ("docker", "veth", "br-", "virbr", "tap", "tun", "zt")


def _get_candidate_urls() -> list[str]:
    """Return all non-loopback IPv4 base URLs for this host.

    Uses psutil.net_if_addrs() to enumerate every network interface so that
    Ethernet LAN addresses (e.g. eth0: 192.168.x.x) are included alongside the
    primary WiFi/outbound address.  The hostname-based approach used by
    socket.getaddrinfo() is unreliable on Linux — it typically resolves to only
    one interface and misses secondary ones.
    """
    prioritized_ips: list[tuple[int, str]] = []

    # Enumerate every network interface via psutil — the only reliable way to
    # capture all IPs when the Pi has both WiFi and Ethernet active.
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET or addr.address.startswith("127."):
                continue
            prioritized_ips.append((_interface_priority(iface), addr.address))

    # If there are any non-docker-ish interfaces, drop docker/tunnel addresses
    # (they're useful in container-only runs but noisy when the host has real
    # LAN IPs). This keeps the UI focused on the top host addresses.
    if any(priority < 100 for priority, _ in prioritized_ips):
        prioritized_ips = [(p, ip) for p, ip in prioritized_ips if p < 100]

    return [f"http://{ip}:{_API_PORT}" for _priority, ip in sorted(prioritized_ips)]


def _interface_priority(iface: str) -> int:
    """Prefer physical LAN interfaces over Docker and tunnel interfaces."""
    normalized = iface.lower()
    if normalized.startswith(_DOCKERISH_INTERFACE_PREFIXES):
        return 100
    if normalized.startswith(("eth", "en", "wlan", "wl")):
        return 0
    return 10


def _get_mdns_name() -> str | None:
    """Return an mDNS-style hostname hint, if the current environment provides one."""
    try:
        return f"{socket.gethostname()}.local"
    except OSError:
        return None


@router.get("/local-access", summary="Get local direct-connection info")
async def get_local_access_info(request: Request) -> LocalAccessInfo:
    """Return local API key and candidate IP URLs for direct (Ethernet/USB-C) access.

    Called by the backend through the WebSocket relay when the user opens the
    camera detail screen.  The response is forwarded to the authenticated frontend
    user so the app can auto-configure local mode without manual key copying.
    """
    runtime = get_request_runtime(request)
    return LocalAccessInfo(
        local_api_key=runtime.runtime_state.local_api_key,
        candidate_urls=_get_candidate_urls(),
        mdns_name=_get_mdns_name(),
    )
