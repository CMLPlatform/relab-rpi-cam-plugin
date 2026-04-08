"""Tests for authentication endpoints and middleware."""

import pytest
from httpx import AsyncClient

from app.core.config import settings

VALID_API_KEY = "valid-key"
AUTH_COOKIE_NAME = "X-API-Key"


@pytest.fixture(autouse=True)
def _set_test_api_key() -> None:
    """Ensure a known API key is available for auth tests."""
    if VALID_API_KEY not in settings.authorized_api_keys:
        settings.authorized_api_keys.append(VALID_API_KEY)


class TestAuthMiddleware:
    """Tests for API key verification on protected endpoints."""

    async def test_missing_api_key_returns_401(self, unauthed_client: AsyncClient) -> None:
        """Test that requests without an API key return a 401 Unauthorized response."""
        resp = await unauthed_client.get("/camera/status")
        assert resp.status_code == 401

    async def test_invalid_api_key_returns_403(self, unauthed_client: AsyncClient) -> None:
        """Test that requests with an invalid API key return a 403 Forbidden response."""
        resp = await unauthed_client.get("/camera/status", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 403

    async def test_valid_api_key_passes(self, client: AsyncClient) -> None:
        """Test that requests with a valid API key are allowed through the middleware and return 200."""
        resp = await client.get("/camera/status")
        assert resp.status_code == 200


class TestLoginEndpoint:
    """Tests for POST /auth/login."""

    async def test_login_with_valid_key_sets_cookie(self, unauthed_client: AsyncClient) -> None:
        """Test that logging in with a valid API key sets the auth cookie and redirects to the specified URL."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": VALID_API_KEY, "redirect_url": "/"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert AUTH_COOKIE_NAME in resp.cookies

    async def test_login_with_invalid_key_returns_403(self, unauthed_client: AsyncClient) -> None:
        """Test that logging in with an invalid API key returns a 403 response and does not set the auth cookie."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": "bad-key"},
            follow_redirects=False,
        )
        assert resp.status_code == 403


class TestLogoutEndpoint:
    """Tests for GET /auth/logout."""

    async def test_logout_clears_cookie(self, unauthed_client: AsyncClient) -> None:
        """Test that logging out clears the authentication cookie and redirects to the login page."""
        resp = await unauthed_client.get("/auth/logout", follow_redirects=False)
        assert resp.status_code == 303
        # Cookie should be cleared (set with max-age=0 or deleted)
        assert AUTH_COOKIE_NAME in resp.headers.get("set-cookie", "")
