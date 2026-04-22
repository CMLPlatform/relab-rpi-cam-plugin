"""Tests for authentication endpoints and middleware."""

import pytest
from httpx import AsyncClient

from app.auth.dependencies import create_session, reload_authorized_hashes
from app.core.runtime import AppRuntime
from app.core.settings import settings

VALID_API_KEY = "valid-key"
AUTH_COOKIE_NAME = "relab_session"
SECURE_ATTR = "Secure"
REQUEST_ID_HEADER = "x-request-id"
ROOT_REDIRECT = "/"
LIVE_TAB_REDIRECT = "/camera?tab=live"


@pytest.fixture(autouse=True)
def _set_test_api_key(app_runtime: AppRuntime) -> None:
    """Ensure a known API key is available for auth tests."""
    app_runtime.runtime_state.add_authorized_api_key(VALID_API_KEY)
    reload_authorized_hashes(app_runtime.runtime_state)


class TestAuthMiddleware:
    """Tests for API key verification on protected endpoints."""

    async def test_missing_api_key_returns_401(self, unauthed_client: AsyncClient) -> None:
        """Test that requests without an API key return a 401 Unauthorized response."""
        resp = await unauthed_client.get("/camera")
        assert resp.status_code == 401

    async def test_invalid_api_key_returns_403(self, unauthed_client: AsyncClient) -> None:
        """Test that requests with an invalid API key return a 403 Forbidden response."""
        resp = await unauthed_client.get("/camera", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 403

    async def test_valid_api_key_passes(self, client: AsyncClient) -> None:
        """Test that requests with a valid API key are allowed through the middleware and return 200."""
        resp = await client.get("/camera")
        assert resp.status_code == 200

    async def test_valid_browser_session_passes(self, unauthed_client: AsyncClient) -> None:
        """A valid browser session should be accepted for local UI requests."""
        unauthed_client.cookies.set(settings.session_cookie_name, create_session())
        resp = await unauthed_client.get("/camera")
        assert resp.status_code == 200


class TestLoginEndpoint:
    """Tests for POST /auth/login."""

    async def test_login_with_valid_key_sets_cookie(self, unauthed_client: AsyncClient) -> None:
        """Test that logging in with a valid API key sets the auth cookie and redirects to the specified URL."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": VALID_API_KEY, "redirect_url": ROOT_REDIRECT},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert AUTH_COOKIE_NAME in resp.cookies
        assert VALID_API_KEY not in resp.headers["set-cookie"]
        assert resp.headers["location"] == ROOT_REDIRECT

    async def test_login_uses_insecure_cookie_for_local_http(self, unauthed_client: AsyncClient) -> None:
        """Local HTTP deployments should not mark auth cookies as secure by default."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": VALID_API_KEY, "redirect_url": ROOT_REDIRECT},
            follow_redirects=False,
        )
        assert SECURE_ATTR not in resp.headers["set-cookie"]

    async def test_login_with_invalid_key_returns_403(self, unauthed_client: AsyncClient) -> None:
        """Test that logging in with an invalid API key returns a 403 response and does not set the auth cookie."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": "bad-key"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    async def test_login_rejects_absolute_redirect_urls(self, unauthed_client: AsyncClient) -> None:
        """Absolute redirect targets should be replaced with the local root."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": VALID_API_KEY, "redirect_url": "https://evil.example/phish?next=/camera"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == ROOT_REDIRECT

    async def test_login_preserves_safe_local_redirect_query(self, unauthed_client: AsyncClient) -> None:
        """Safe local redirects may keep their query string."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": VALID_API_KEY, "redirect_url": LIVE_TAB_REDIRECT},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == LIVE_TAB_REDIRECT

    async def test_login_rejects_non_absolute_local_paths(self, unauthed_client: AsyncClient) -> None:
        """Relative redirect targets without a leading slash should fall back to root."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": VALID_API_KEY, "redirect_url": "camera"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == ROOT_REDIRECT


class TestLogoutEndpoint:
    """Tests for POST /auth/logout."""

    async def test_logout_clears_cookie(self, unauthed_client: AsyncClient) -> None:
        """Test that logging out clears the authentication cookie and redirects to the login page."""
        unauthed_client.cookies.set(settings.session_cookie_name, create_session())
        resp = await unauthed_client.post("/auth/logout", follow_redirects=False)
        assert resp.status_code == 303
        # Cookie should be cleared (set with max-age=0 or deleted)
        assert AUTH_COOKIE_NAME in resp.headers.get("set-cookie", "")


class TestCorsConfig:
    """Tests for CORS behavior."""

    async def test_preflight_allows_auth_header(self, unauthed_client: AsyncClient) -> None:
        """Configured auth headers should be accepted in CORS preflight responses."""
        origin = str(settings.allowed_cors_origins[0]).rstrip("/")
        resp = await unauthed_client.options(
            "/camera",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": settings.auth_key_name,
            },
        )
        assert resp.status_code == 200
        assert settings.auth_key_name.lower() in resp.headers["access-control-allow-headers"].lower()

    async def test_preflight_allows_request_id_for_local_capture_requests(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Direct-local browser requests should be allowed to send X-Request-ID."""
        origin = str(settings.allowed_cors_origins[0]).rstrip("/")
        resp = await unauthed_client.options(
            "/captures",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": f"{settings.auth_key_name}, X-Request-ID",
            },
        )
        assert resp.status_code == 200
        allow_headers = resp.headers["access-control-allow-headers"].lower()
        assert settings.auth_key_name.lower() in allow_headers
        assert REQUEST_ID_HEADER in allow_headers
