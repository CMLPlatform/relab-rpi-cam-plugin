"""Tests for authentication endpoints and middleware."""

import logging
from typing import Any, cast

import pytest
from httpx import AsyncClient
from pydantic import HttpUrl

from app.auth.dependencies import (
    SESSION_COOKIE_MAX_AGE_SECONDS,
    create_session,
    has_valid_session,
    reload_authorized_hashes,
)
from app.core.runtime import AppRuntime
from app.core.settings import settings
from tests.constants import NO_STORE_CACHE_CONTROL

VALID_API_KEY = "valid-key"
BAD_API_KEY = "bad-key"
AUTH_COOKIE_NAME = "relab_session"
SECURE_ATTR = "Secure"
HTTPONLY_ATTR = "HttpOnly"
SAMESITE_ATTR = "SameSite=lax"
PATH_ATTR = "Path=/"
MAX_AGE_ATTR = "Max-Age="
LOGOUT_FORM_MARKER = 'class="site-header__logout"'
REQUEST_ID_HEADER = "x-request-id"
ALLOW_ORIGIN_HEADER = "access-control-allow-origin"
ALLOW_PRIVATE_NETWORK_HEADER = "access-control-allow-private-network"
WILDCARD_ORIGIN = "*"
TRUE_HEADER_VALUE = "true"
NO_CACHE_PRAGMA = "no-cache"
EXPIRES_IMMEDIATELY = "0"
CLEAR_SITE_DATA_HEADER = "clear-site-data"
CLEAR_SITE_DATA_VALUE = '"cache", "storage"'
ROOT_REDIRECT = "/"
LIVE_TAB_REDIRECT = "/camera?tab=live"
SAME_ORIGIN = "http://test"
CROSS_SITE_ORIGIN = "https://evil.example"

PROTECTED_ENDPOINTS: tuple[tuple[str, str, object | None], ...] = (
    ("GET", "/camera", None),
    ("GET", "/camera/controls", None),
    ("POST", "/captures", {}),
    ("POST", "/preview/start", None),
    ("POST", "/preview/stop", None),
    ("GET", "/streams/youtube", None),
    ("POST", "/streams/youtube", {"stream_key": "secret-stream", "broadcast_key": "public-broadcast"}),
    ("DELETE", "/streams/youtube", None),
    ("DELETE", "/pairing", None),
    ("POST", "/pairing/code", None),
    ("GET", "/local-key", None),
    ("GET", "/system/local-access", None),
    ("GET", "/system/telemetry", None),
    ("GET", "/metrics", None),
)

PUBLIC_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GET", "/"),
    ("GET", "/setup"),
    ("GET", "/pairing/state"),
)


@pytest.fixture(autouse=True)
def _set_test_api_key(app_runtime: AppRuntime) -> None:
    """Ensure a known API key is available for auth tests."""
    app_runtime.runtime_state.add_authorized_api_key(VALID_API_KEY)
    reload_authorized_hashes(app_runtime.runtime_state)


def _log_record(caplog: pytest.LogCaptureFixture, event: str) -> Any:
    """Return a structured log record with test-owned dynamic attributes."""
    return cast("Any", next(record for record in caplog.records if getattr(record, "event", "") == event))


class TestAuthMiddleware:
    """Tests for API key verification on protected endpoints."""

    async def test_unsupported_http_methods_use_framework_405(self, unauthed_client: AsyncClient) -> None:
        """TRACE should be blocked consistently before auth or route handling."""
        resp = await unauthed_client.request("TRACE", "/camera")

        assert resp.status_code == 405

    @pytest.mark.parametrize("path", ["/", "/static/logo.png"])
    async def test_trace_is_blocked_for_public_and_static_routes(
        self,
        unauthed_client: AsyncClient,
        path: str,
    ) -> None:
        """TRACE should stay unsupported on every local HTTP surface."""
        resp = await unauthed_client.request("TRACE", path)

        assert resp.status_code == 405

    async def test_missing_api_key_returns_401(self, unauthed_client: AsyncClient) -> None:
        """Test that requests without an API key return a 401 Unauthorized response."""
        resp = await unauthed_client.get("/camera")
        assert resp.status_code == 401

    async def test_missing_api_key_logs_auth_failure(
        self,
        unauthed_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Missing protected-route credentials should be logged without secrets."""
        with caplog.at_level(logging.WARNING):
            resp = await unauthed_client.get("/camera")

        assert resp.status_code == 401
        record = _log_record(caplog, "auth.request")
        assert record.outcome == "failure"
        assert record.auth_method == "missing"
        assert record.method == "GET"
        assert record.path == "/camera"
        assert record.status_code == 401

    async def test_invalid_api_key_returns_403(self, unauthed_client: AsyncClient) -> None:
        """Test that requests with an invalid API key return a 403 Forbidden response."""
        resp = await unauthed_client.get("/camera", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 403

    async def test_invalid_api_key_logs_auth_failure_without_key(
        self,
        unauthed_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Invalid API-key attempts should log metadata but never the submitted key."""
        with caplog.at_level(logging.WARNING):
            resp = await unauthed_client.get("/camera", headers={"X-API-Key": BAD_API_KEY})

        assert resp.status_code == 403
        record = _log_record(caplog, "auth.request")
        assert record.outcome == "failure"
        assert record.auth_method == "api_key"
        assert record.status_code == 403
        assert BAD_API_KEY not in caplog.text

    async def test_valid_api_key_passes(self, client: AsyncClient) -> None:
        """Test that requests with a valid API key are allowed through the middleware and return 200."""
        resp = await client.get("/camera")
        assert resp.status_code == 200

    async def test_valid_api_key_does_not_log_routine_success(
        self,
        unauthed_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Routine protected-route success should not add noisy auth.request logs."""
        with caplog.at_level(logging.INFO):
            resp = await unauthed_client.get("/camera", headers={"X-API-Key": VALID_API_KEY})

        assert resp.status_code == 200
        assert all(
            getattr(record, "event", "") != "auth.request" or getattr(record, "outcome", "") != "success"
            for record in caplog.records
        )
        assert VALID_API_KEY not in caplog.text

    async def test_valid_browser_session_passes(self, unauthed_client: AsyncClient) -> None:
        """A valid browser session should be accepted for local UI requests."""
        unauthed_client.cookies.set(settings.browser_session_cookie_name, create_session())
        resp = await unauthed_client.get("/camera")
        assert resp.status_code == 200

    async def test_secure_browser_session_cookie_passes(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTPS deployments should accept the __Host-prefixed session cookie."""
        monkeypatch.setattr(settings, "auth_cookie_secure", True)
        unauthed_client.cookies.set(settings.browser_session_cookie_name, create_session())

        resp = await unauthed_client.get("/camera")

        assert resp.status_code == 200

    async def test_cookie_authenticated_cross_site_write_is_rejected(self, unauthed_client: AsyncClient) -> None:
        """Browser-session writes from another site should fail CSRF checks."""
        unauthed_client.cookies.set(settings.browser_session_cookie_name, create_session())

        resp = await unauthed_client.put(
            "/camera/focus",
            json={"mode": "manual", "lens_position": 1.5},
            headers={"Origin": CROSS_SITE_ORIGIN},
        )

        assert resp.status_code == 403

    async def test_cookie_authenticated_cross_site_write_logs_authorization_failure(
        self,
        unauthed_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """CSRF denials should be logged as failed authorization decisions."""
        unauthed_client.cookies.set(settings.browser_session_cookie_name, create_session())

        with caplog.at_level(logging.WARNING):
            resp = await unauthed_client.put(
                "/camera/focus",
                json={"mode": "manual", "lens_position": 1.5},
                headers={"Origin": CROSS_SITE_ORIGIN},
            )

        assert resp.status_code == 403
        record = _log_record(caplog, "auth.csrf")
        assert record.outcome == "failure"
        assert record.auth_method == "browser_session"
        assert record.method == "PUT"
        assert record.path == "/camera/focus"

    async def test_cookie_authenticated_same_origin_write_passes(self, unauthed_client: AsyncClient) -> None:
        """Same-origin browser-session writes should keep working."""
        unauthed_client.cookies.set(settings.browser_session_cookie_name, create_session())

        resp = await unauthed_client.put(
            "/camera/focus",
            json={"mode": "manual", "lens_position": 1.5},
            headers={"Origin": SAME_ORIGIN},
        )

        assert resp.status_code == 200

    async def test_api_key_cross_site_write_bypasses_csrf(self, unauthed_client: AsyncClient) -> None:
        """Explicit API-key writes are not ambient browser auth and should bypass CSRF."""
        resp = await unauthed_client.put(
            "/camera/focus",
            json={"mode": "manual", "lens_position": 1.5},
            headers={"X-API-Key": VALID_API_KEY, "Origin": CROSS_SITE_ORIGIN},
        )

        assert resp.status_code == 200

    async def test_dynamic_responses_are_not_cacheable(self, client: AsyncClient) -> None:
        """Authenticated dynamic responses should not be stored by browser or proxy caches."""
        resp = await client.get("/camera")

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == NO_STORE_CACHE_CONTROL
        assert resp.headers["pragma"] == NO_CACHE_PRAGMA
        assert resp.headers["expires"] == EXPIRES_IMMEDIATELY

    async def test_local_access_response_is_not_cacheable(
        self,
        client: AsyncClient,
        app_runtime: AppRuntime,
    ) -> None:
        """The relay-forwarded local API key bootstrap response must not be cached."""
        app_runtime.runtime_state.set_local_api_key("local-key")
        resp = await client.get("/system/local-access")

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == NO_STORE_CACHE_CONTROL
        assert resp.headers["pragma"] == NO_CACHE_PRAGMA
        assert resp.headers["expires"] == EXPIRES_IMMEDIATELY


class TestAuthorizationRouteMatrix:
    """Regression checks for public and protected local API surfaces."""

    @pytest.mark.parametrize(("method", "path", "json_body"), PROTECTED_ENDPOINTS)
    async def test_sensitive_endpoints_reject_unauthenticated_requests(
        self,
        unauthed_client: AsyncClient,
        method: str,
        path: str,
        json_body: object | None,
    ) -> None:
        """Function-level access must require an explicit API key or browser session."""
        resp = await unauthed_client.request(method, path, json=json_body)

        assert resp.status_code in {401, 403}

    @pytest.mark.parametrize(("method", "path"), PUBLIC_ENDPOINTS)
    async def test_intentionally_public_endpoints_remain_reachable(
        self,
        unauthed_client: AsyncClient,
        method: str,
        path: str,
    ) -> None:
        """Public setup/status pages stay reachable before pairing or login."""
        resp = await unauthed_client.request(method, path)

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

    async def test_login_with_valid_key_logs_success_without_key(
        self,
        unauthed_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Successful browser login should log the event without credential material."""
        with caplog.at_level(logging.INFO):
            resp = await unauthed_client.post(
                "/auth/login",
                data={"api_key": VALID_API_KEY, "redirect_url": ROOT_REDIRECT},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        record = _log_record(caplog, "auth.login")
        assert record.outcome == "success"
        assert record.auth_method == "api_key"
        assert record.status_code == 303
        assert VALID_API_KEY not in caplog.text

    async def test_login_rotates_existing_browser_session(self, unauthed_client: AsyncClient) -> None:
        """Successful login should invalidate the previous browser session token."""
        old_token = create_session()
        unauthed_client.cookies.set(settings.browser_session_cookie_name, old_token)

        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": VALID_API_KEY, "redirect_url": ROOT_REDIRECT},
            follow_redirects=False,
        )

        new_token = resp.cookies[settings.browser_session_cookie_name]
        assert resp.status_code == 303
        assert new_token != old_token
        assert has_valid_session(old_token) is False
        assert has_valid_session(new_token) is True

    async def test_login_cookie_max_age_matches_absolute_lifetime(self, unauthed_client: AsyncClient) -> None:
        """The browser cookie should not outlive the server-side absolute session lifetime."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": VALID_API_KEY, "redirect_url": ROOT_REDIRECT},
            follow_redirects=False,
        )

        assert f"{MAX_AGE_ATTR}{SESSION_COOKIE_MAX_AGE_SECONDS}" in resp.headers["set-cookie"]

    async def test_login_uses_insecure_cookie_for_local_http(self, unauthed_client: AsyncClient) -> None:
        """Local HTTP deployments should not mark auth cookies as secure by default."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": VALID_API_KEY, "redirect_url": ROOT_REDIRECT},
            follow_redirects=False,
        )
        assert SECURE_ATTR not in resp.headers["set-cookie"]
        assert AUTH_COOKIE_NAME in resp.headers["set-cookie"]

    async def test_login_uses_host_prefixed_secure_cookie_for_https(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTPS deployments should use an ASVS-aligned host-prefixed cookie."""
        monkeypatch.setattr(settings, "base_url", HttpUrl("https://camera.example/"))
        monkeypatch.setattr(settings, "auth_cookie_secure", None)

        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": VALID_API_KEY, "redirect_url": ROOT_REDIRECT},
            follow_redirects=False,
        )

        cookie = resp.headers["set-cookie"]
        assert resp.status_code == 303
        assert settings.browser_session_cookie_name in cookie
        assert AUTH_COOKIE_NAME not in resp.cookies
        assert SECURE_ATTR in cookie
        assert HTTPONLY_ATTR in cookie
        assert SAMESITE_ATTR in cookie
        assert PATH_ATTR in cookie

    async def test_login_with_invalid_key_returns_403(self, unauthed_client: AsyncClient) -> None:
        """Test that logging in with an invalid API key returns a 403 response and does not set the auth cookie."""
        resp = await unauthed_client.post(
            "/auth/login",
            data={"api_key": "bad-key"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    async def test_login_with_invalid_key_logs_failure_without_key(
        self,
        unauthed_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Failed browser login should log the event without credential material."""
        with caplog.at_level(logging.WARNING):
            resp = await unauthed_client.post(
                "/auth/login",
                data={"api_key": BAD_API_KEY},
                follow_redirects=False,
            )

        assert resp.status_code == 403
        record = _log_record(caplog, "auth.login")
        assert record.outcome == "failure"
        assert record.auth_method == "api_key"
        assert record.status_code == 403
        assert BAD_API_KEY not in caplog.text

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
        unauthed_client.cookies.set(settings.browser_session_cookie_name, create_session())
        resp = await unauthed_client.post(
            "/auth/logout",
            headers={"Origin": SAME_ORIGIN},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        # Cookie should be cleared (set with max-age=0 or deleted)
        assert AUTH_COOKIE_NAME in resp.headers.get("set-cookie", "")
        assert resp.headers["cache-control"] == NO_STORE_CACHE_CONTROL
        assert resp.headers["pragma"] == NO_CACHE_PRAGMA
        assert resp.headers["expires"] == EXPIRES_IMMEDIATELY
        assert resp.headers[CLEAR_SITE_DATA_HEADER] == CLEAR_SITE_DATA_VALUE

    async def test_logout_logs_session_invalidation(
        self,
        unauthed_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Logout should log that a browser session was invalidated."""
        unauthed_client.cookies.set(settings.browser_session_cookie_name, create_session())

        with caplog.at_level(logging.INFO):
            resp = await unauthed_client.post(
                "/auth/logout",
                headers={"Origin": SAME_ORIGIN},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        record = _log_record(caplog, "auth.logout")
        assert record.outcome == "success"
        assert record.auth_method == "browser_session"
        assert record.status_code == 303

    async def test_logout_clears_secure_cookie_name(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTPS deployments should delete the active host-prefixed cookie name."""
        monkeypatch.setattr(settings, "auth_cookie_secure", True)
        unauthed_client.cookies.set(settings.browser_session_cookie_name, create_session())

        resp = await unauthed_client.post(
            "/auth/logout",
            headers={"Origin": SAME_ORIGIN},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert settings.browser_session_cookie_name in resp.headers.get("set-cookie", "")

    async def test_logout_rejects_cross_site_cookie_write(self, unauthed_client: AsyncClient) -> None:
        """Logout is cookie-authenticated and should enforce the same CSRF policy."""
        unauthed_client.cookies.set(settings.browser_session_cookie_name, create_session())

        resp = await unauthed_client.post(
            "/auth/logout",
            headers={"Origin": CROSS_SITE_ORIGIN},
            follow_redirects=False,
        )

        assert resp.status_code == 403


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

    async def test_local_mode_cors_does_not_allow_arbitrary_origins(self, unauthed_client: AsyncClient) -> None:
        """Local mode should not advertise wildcard browser access."""
        resp = await unauthed_client.options(
            "/camera",
            headers={
                "Origin": CROSS_SITE_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": settings.auth_key_name,
            },
        )
        assert resp.status_code == 400
        assert resp.headers.get(ALLOW_ORIGIN_HEADER) != WILDCARD_ORIGIN
        assert ALLOW_PRIVATE_NETWORK_HEADER not in resp.headers

    async def test_private_network_access_is_limited_to_allowed_cors_origins(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """PNA should only be advertised on accepted CORS paths."""
        origin = str(settings.allowed_cors_origins[0]).rstrip("/")
        resp = await unauthed_client.options(
            "/camera",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": settings.auth_key_name,
                "Access-Control-Request-Private-Network": TRUE_HEADER_VALUE,
            },
        )
        assert resp.status_code == 200
        assert resp.headers[ALLOW_ORIGIN_HEADER] == origin
        assert resp.headers[ALLOW_PRIVATE_NETWORK_HEADER] == TRUE_HEADER_VALUE
