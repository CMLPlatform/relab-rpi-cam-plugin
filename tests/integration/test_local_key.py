"""Tests for the local-only direct-connection key endpoint."""

import pytest
from httpx import AsyncClient

from app.core.runtime import AppRuntime
from app.pairing.routers import local_key as local_key_router

LOCAL_KEY_PATH = "/local-key"
TEST_LOCAL_KEY = "test-local-api-key"
NO_STORE_CACHE_CONTROL = "no-store"
LOCAL_KEY_ATTACHMENT = 'attachment; filename="local-api-key.txt"'


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
        client: AsyncClient,
    ) -> None:
        """Authenticated local clients should receive the plain-text key."""
        monkeypatch.setattr(local_key_router, "is_local_client", lambda _host: True)
        resp = await client.get(LOCAL_KEY_PATH)
        assert resp.status_code == 200
        assert resp.text.strip() == TEST_LOCAL_KEY
        assert resp.headers["cache-control"] == NO_STORE_CACHE_CONTROL
        assert resp.headers["content-disposition"] == LOCAL_KEY_ATTACHMENT

    async def test_rejects_unauthenticated_local_clients(
        self,
        monkeypatch: pytest.MonkeyPatch,
        unauthed_client: AsyncClient,
    ) -> None:
        """Local network access alone should not reveal the key."""
        monkeypatch.setattr(local_key_router, "is_local_client", lambda _host: True)
        resp = await unauthed_client.get(LOCAL_KEY_PATH)
        assert resp.status_code == 401

    async def test_rejects_non_local_clients(
        self,
        monkeypatch: pytest.MonkeyPatch,
        client: AsyncClient,
    ) -> None:
        """Remote clients should be blocked from reading the key."""
        monkeypatch.setattr(local_key_router, "is_local_client", lambda _host: False)
        resp = await client.get(LOCAL_KEY_PATH)
        assert resp.status_code == 403
