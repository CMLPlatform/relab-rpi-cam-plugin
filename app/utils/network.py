"""Network helpers shared by local-only public routes."""

import socket
from ipaddress import ip_address

import psutil

_DOCKERISH_INTERFACE_PREFIXES = ("docker", "veth", "br-", "virbr", "tap", "tun", "zt")
_LOCALHOST = "localhost"


def find_lan_ips() -> list[str]:
    """Return this host's non-loopback IPv4 addresses, physical LANs first.

    Enumerates every interface via psutil.net_if_addrs() — the only reliable way
    to capture all IPs when the Pi has both WiFi and Ethernet active. Docker and
    tunnel addresses are dropped whenever a real LAN interface is present.
    """
    prioritized: list[tuple[int, str]] = []
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET or addr.address.startswith("127."):
                continue
            prioritized.append((_interface_priority(iface), addr.address))

    if any(priority < 100 for priority, _ in prioritized):
        prioritized = [(p, ip) for p, ip in prioritized if p < 100]

    return [ip for _priority, ip in sorted(prioritized)]


def _interface_priority(iface: str) -> int:
    """Prefer physical LAN interfaces over Docker and tunnel interfaces."""
    normalized = iface.lower()
    if normalized.startswith(_DOCKERISH_INTERFACE_PREFIXES):
        return 100
    if normalized.startswith(("eth", "en", "wlan", "wl")):
        return 0
    return 10


def is_local_client(host: str | None) -> bool:
    """Return whether a request came from a local network address."""
    if not host:
        return False
    try:
        client_ip = ip_address(host)
    except ValueError:
        return host == _LOCALHOST
    return client_ip.is_loopback or client_ip.is_private or client_ip.is_link_local
