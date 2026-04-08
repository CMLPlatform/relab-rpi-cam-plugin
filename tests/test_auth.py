"""Tests for authentication endpoints and middleware."""

import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.fixture(autouse=True)
def _set_test_api_key() -> None:
    """Ensure a known API key is available for auth tests."""
    if "valid-key" not in settings.authorized_api_keys:
        settings.authorized_api_keys.append("valid-key")


class TestAuthMiddleware:
    """Tests for API key verification on protected endpoints."""

    async def test_missing_api_key_returns_401(self, unauthed_client: AsyncClient) -> None:
        resp = await unauthed_client.get("/camera/status")
        assert resp.status_code == 401

    async def test_invalid_api_key_returns_403(self, unauthed_client: AsyncClient) -> None:
        resp = await unauthed_client.get("/camera/status", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 403

    async def test_valid_api_key_passes(self, client: AsyncClient) -> None:
        resp = await client.get("/camera/status")
        assert resp.status_code == 200


class TestLoginEndpoint:
    """Tests for POST /auth/login."""

    async def test_login_with_valid_key_sets_cookie(self, unauthed_client: AsyncClient) -> None:
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": "valid-key", "redirect_url": "/"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "X-API-Key" in resp.cookies

    async def test_login_with_invalid_key_returns_403(self, unauthed_client: AsyncClient) -> None:
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": "bad-key"},
            follow_redirects=False,
        )
        assert resp.status_code == 403


class TestLogoutEndpoint:
    """Tests for GET /auth/logout."""

    async def test_logout_clears_cookie(self, unauthed_client: AsyncClient) -> None:
        resp = await unauthed_client.get("/auth/logout", follow_redirects=False)
        assert resp.status_code == 303
        # Cookie should be cleared (set with max-age=0 or deleted)
        assert "X-API-Key" in resp.headers.get("set-cookie", "")
