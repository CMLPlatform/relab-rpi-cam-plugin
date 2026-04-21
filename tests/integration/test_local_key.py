"""Tests for the local-only direct-connection key endpoint."""

import pytest
from httpx import AsyncClient

from app.core.runtime import AppRuntime
from app.pairing.routers import local_key as local_key_router

LOCAL_KEY_PATH = "/local-key"
TEST_LOCAL_KEY = "test-local-api-key"


@pytest.fixture
def _local_api_key(app_runtime: AppRuntime) -> None:
    """Ensure the endpoint has a stable key to return."""
    app_runtime.runtime_state.set_local_api_key(TEST_LOCAL_KEY)


@pytest.mark.usefixtures("_local_api_key")
class TestLocalKeyEndpoint:
    """Tests for GET /local-key."""

    async def test_returns_key_to_local_clients(
        self,
        monkeypatch: pytest.MonkeyPatch,
        unauthed_client: AsyncClient,
    ) -> None:
        """Local clients should receive the plain-text key."""
        monkeypatch.setattr(local_key_router, "is_local_client", lambda _host: True)
        resp = await unauthed_client.get(LOCAL_KEY_PATH)
        assert resp.status_code == 200
        assert resp.text.strip() == TEST_LOCAL_KEY

    async def test_rejects_non_local_clients(
        self,
        monkeypatch: pytest.MonkeyPatch,
        unauthed_client: AsyncClient,
    ) -> None:
        """Remote clients should be blocked from reading the key."""
        monkeypatch.setattr(local_key_router, "is_local_client", lambda _host: False)
        resp = await unauthed_client.get(LOCAL_KEY_PATH)
        assert resp.status_code == 403
