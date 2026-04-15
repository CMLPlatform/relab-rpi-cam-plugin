"""Tests for local direct-connection helpers."""

from __future__ import annotations

from types import SimpleNamespace

import socket

from app.api.routers.local_access import _get_candidate_urls


class TestGetCandidateUrls:
    """Tests for _get_candidate_urls."""

    def test_prefers_physical_interfaces_over_docker_bridges(self, monkeypatch) -> None:
        """A Docker bridge should not outrank a real LAN interface."""
        monkeypatch.setattr(
            "app.api.routers.local_access.psutil.net_if_addrs",
            lambda: {
                "docker0": [SimpleNamespace(family=socket.AF_INET, address="172.17.0.1")],
                "eth0": [SimpleNamespace(family=socket.AF_INET, address="192.168.1.50")],
                "lo": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
            },
        )

        urls = _get_candidate_urls()

        assert urls == ["http://192.168.1.50:8018"]
