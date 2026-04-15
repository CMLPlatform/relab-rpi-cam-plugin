"""Integration tests for the telemetry endpoint."""

from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from app.utils import telemetry as telemetry_mod
from tests.constants import (
    TELEMETRY_CPU_PERCENT,
    TELEMETRY_CPU_TEMP,
    TELEMETRY_DISK_PERCENT,
    TELEMETRY_MEM_PERCENT,
    TELEMETRY_THERMAL_NORMAL,
    TIMESTAMP_KEY,
)


@pytest.fixture(autouse=True)
def _mock_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from the host's real CPU/memory/disk state."""

    def _cpu_percent(*, interval: object = None, percpu: bool = False) -> float:
        del interval, percpu
        return 7.5

    monkeypatch.setattr(telemetry_mod, "_read_cpu_temp_c", lambda: 48.2)
    monkeypatch.setattr(telemetry_mod.psutil, "cpu_percent", _cpu_percent)
    monkeypatch.setattr(telemetry_mod.psutil, "virtual_memory", lambda: MagicMock(percent=31.0))
    monkeypatch.setattr(
        telemetry_mod.shutil,
        "disk_usage",
        lambda _path: MagicMock(used=250, total=1000),
    )


class TestTelemetryRoute:
    """Tests for GET /telemetry."""

    async def test_returns_200_and_schema(self, client: AsyncClient) -> None:
        """The endpoint must return a well-formed TelemetrySnapshot payload."""
        resp = await client.get("/telemetry")
        assert resp.status_code == 200

        data = resp.json()
        assert data["cpu_temp_c"] == TELEMETRY_CPU_TEMP
        assert data["cpu_percent"] == TELEMETRY_CPU_PERCENT
        assert data["mem_percent"] == TELEMETRY_MEM_PERCENT
        assert data["disk_percent"] == TELEMETRY_DISK_PERCENT
        assert data["thermal_state"] == TELEMETRY_THERMAL_NORMAL
        assert data["preview_fps"] is None
        assert data["preview_sessions"] == 0
        assert data["current_preview_size"] is None
        assert TIMESTAMP_KEY in data

    async def test_requires_auth(self, unauthed_client: AsyncClient) -> None:
        """Telemetry must sit behind the standard verify_request dependency."""
        resp = await unauthed_client.get("/telemetry")
        assert resp.status_code in {401, 403}
