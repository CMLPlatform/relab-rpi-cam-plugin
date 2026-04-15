"""Network helpers shared by local-only public routes."""

from __future__ import annotations

from ipaddress import ip_address

_LOCALHOST_NAME = "localhost"


def is_local_client(host: str | None) -> bool:
    """Return whether a request came from a local network address."""
    if not host:
        return False
    try:
        client_ip = ip_address(host)
    except ValueError:
        return host == _LOCALHOST_NAME
    return client_ip.is_loopback or client_ip.is_private or client_ip.is_link_local
