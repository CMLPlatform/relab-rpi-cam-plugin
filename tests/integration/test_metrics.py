"""Integration tests for the unauthenticated /metrics endpoint."""

from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from app.observability import telemetry as telemetry_mod
from tests.constants import (
    MET_CPU,
    MET_CPU_18_5,
    MET_CPU_TEMP_62_0,
    MET_DISK_12_0,
    MET_MEM_44_0,
    MET_THERMAL_WARM,
)


@pytest.fixture(autouse=True)
def _mock_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from the host's real system stats."""
    monkeypatch.setattr(telemetry_mod, "_read_cpu_temp_c", lambda: 62.0)
    monkeypatch.setattr(telemetry_mod.psutil, "cpu_percent", MagicMock(return_value=18.5))
    monkeypatch.setattr(telemetry_mod.psutil, "virtual_memory", lambda: MagicMock(percent=44.0))
    monkeypatch.setattr(
        telemetry_mod.shutil,
        "disk_usage",
        lambda _path: MagicMock(used=120, total=1000),
    )


class TestMetricsRoute:
    """Tests for GET /metrics."""

    async def test_returns_prometheus_text_format(self, client: AsyncClient) -> None:
        """The endpoint must return text/plain Prometheus exposition."""
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")

        body = resp.text
        assert MET_CPU_18_5 in body
        assert MET_MEM_44_0 in body
        assert MET_DISK_12_0 in body
        assert MET_CPU_TEMP_62_0 in body
        assert MET_THERMAL_WARM in body

    async def test_unauthenticated_access_allowed(self, unauthed_client: AsyncClient) -> None:
        """/metrics must be reachable without the API key — Alloy scrapes it directly."""
        resp = await unauthed_client.get("/metrics")
        assert resp.status_code == 200
        assert MET_CPU in resp.text
