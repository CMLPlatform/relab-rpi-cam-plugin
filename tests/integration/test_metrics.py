"""Integration tests for the unauthenticated /metrics endpoint."""

from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from app.utils import telemetry as telemetry_mod


@pytest.fixture(autouse=True)
def _mock_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from the host's real system stats."""
    monkeypatch.setattr(telemetry_mod, "_read_cpu_temp_c", lambda: 62.0)
    monkeypatch.setattr(telemetry_mod.psutil, "cpu_percent", lambda _interval=None: 18.5)
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
        assert "rpi_cam_cpu_percent 18.5" in body
        assert "rpi_cam_mem_percent 44.0" in body
        assert "rpi_cam_disk_percent 12.0" in body
        assert "rpi_cam_cpu_temp_celsius 62.0" in body
        assert 'rpi_cam_thermal_state{state="warm"} 1' in body

    async def test_unauthenticated_access_allowed(self, unauthed_client: AsyncClient) -> None:
        """/metrics must be reachable without the API key — Alloy scrapes it directly."""
        resp = await unauthed_client.get("/metrics")
        assert resp.status_code == 200
        assert "rpi_cam_cpu_percent" in resp.text
