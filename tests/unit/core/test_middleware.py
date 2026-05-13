"""Tests for security middleware helpers."""

from __future__ import annotations

import pytest
from fastapi import Request
from starlette.responses import Response

import app.core.middleware as middleware_mod

_BLOB_WORKER_CSP_DIRECTIVE = "worker-src 'self' blob:"
_WORKER_SRC_DIRECTIVE = "worker-src"
_DEFAULT_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_OBJECT_SRC_NONE_DIRECTIVE = "object-src 'none'"
_SETUP_CONNECT_SELF_DIRECTIVE = "connect-src 'self'"
_HTTP_SCHEME_SOURCE = "http:"
_WS_SCHEME_SOURCE = "ws:"
_WSS_SCHEME_SOURCE = "wss:"
TEST_API_KEY = "test-secret-api-key"
OTHER_TEST_API_KEY = "other-secret-api-key"
TEST_COOKIE_VALUE = "test-session-token"


def _request(method: str, path: str, *, headers: dict[str, str] | None = None, client: str = "192.0.2.10") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()],
            "client": (client, 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


async def _response(status_code: int = 204) -> Response:
    return Response(status_code=status_code)


class TestContentSecurityPolicy:
    """Tests for route-specific CSP generation."""

    def test_setup_page_allows_blob_workers_for_hls(self) -> None:
        """Setup routes should allow blob workers used by hls.js."""
        csp = middleware_mod._content_security_policy_for_path("/")

        assert _BLOB_WORKER_CSP_DIRECTIVE in csp
        assert _OBJECT_SRC_NONE_DIRECTIVE in csp
        assert _SETUP_CONNECT_SELF_DIRECTIVE in csp
        assert _HTTP_SCHEME_SOURCE not in csp
        assert _WS_SCHEME_SOURCE not in csp
        assert _WSS_SCHEME_SOURCE not in csp

    def test_default_policy_does_not_add_blob_worker_support(self) -> None:
        """Non-setup routes should keep the stricter baseline policy."""
        csp = middleware_mod._content_security_policy_for_path("/api/status")

        assert csp == _DEFAULT_CSP
        assert _WORKER_SRC_DIRECTIVE not in csp


class TestRateLimiter:
    """Tests for anti-automation rate limiting."""

    @pytest.mark.asyncio
    async def test_login_failed_attempt_limit_is_preserved(self) -> None:
        """Login should still rate-limit after repeated failed attempts."""
        limiter = middleware_mod.RateLimiter()
        request = _request("POST", "/auth/login")

        for _ in range(limiter.LOGIN_MAX_FAILED_ATTEMPTS):
            response = await limiter.handle(request, lambda _request: _response(403))
            assert response.status_code == 403

        response = await limiter.handle(request, lambda _request: _response(403))

        assert response.status_code == 429

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/captures"),
            ("POST", "/streams/youtube"),
            ("POST", "/preview/start"),
            ("POST", "/preview/stop"),
            ("PATCH", "/camera/controls"),
            ("PUT", "/camera/focus"),
            ("DELETE", "/pairing"),
            ("POST", "/pairing/code"),
        ],
    )
    async def test_expensive_mutating_routes_are_rate_limited(self, method: str, path: str) -> None:
        """Expensive business actions should have an all-attempt fixed-window limit."""
        limiter = middleware_mod.RateLimiter()
        request = _request(method, path, headers={"X-API-Key": TEST_API_KEY})

        for _ in range(limiter.ACTION_MAX_ATTEMPTS):
            response = await limiter.handle(request, lambda _request: _response())
            assert response.status_code == 204

        response = await limiter.handle(request, lambda _request: _response())

        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_hls_and_status_routes_are_not_rate_limited(self) -> None:
        """Read-heavy preview/status routes should not consume action-rate buckets."""
        limiter = middleware_mod.RateLimiter()
        request = _request("GET", "/preview/hls/cam-preview/index.m3u8")

        for _ in range(limiter.ACTION_MAX_ATTEMPTS + 1):
            response = await limiter.handle(request, lambda _request: _response())
            assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_different_api_keys_use_independent_action_buckets(self) -> None:
        """Per-client action buckets should be keyed by API credential where available."""
        limiter = middleware_mod.RateLimiter()
        first_request = _request("POST", "/captures", headers={"X-API-Key": TEST_API_KEY})
        second_request = _request("POST", "/captures", headers={"X-API-Key": OTHER_TEST_API_KEY})

        for _ in range(limiter.ACTION_MAX_ATTEMPTS):
            response = await limiter.handle(first_request, lambda _request: _response())
            assert response.status_code == 204

        assert (await limiter.handle(first_request, lambda _request: _response())).status_code == 429
        assert (await limiter.handle(second_request, lambda _request: _response())).status_code == 204

    @pytest.mark.asyncio
    async def test_different_action_routes_use_independent_buckets(self) -> None:
        """One expensive action should not consume another action's route budget."""
        limiter = middleware_mod.RateLimiter()
        captures_request = _request("POST", "/captures", headers={"X-API-Key": TEST_API_KEY})
        stream_request = _request("POST", "/streams/youtube", headers={"X-API-Key": TEST_API_KEY})

        for _ in range(limiter.ACTION_MAX_ATTEMPTS):
            response = await limiter.handle(captures_request, lambda _request: _response())
            assert response.status_code == 204

        assert (await limiter.handle(captures_request, lambda _request: _response())).status_code == 429
        assert (await limiter.handle(stream_request, lambda _request: _response())).status_code == 204

    @pytest.mark.asyncio
    async def test_rate_limit_buckets_do_not_store_raw_api_keys_or_sessions(self) -> None:
        """Limiter keys should not retain raw credentials in memory."""
        limiter = middleware_mod.RateLimiter()
        request = _request(
            "POST",
            "/captures",
            headers={"X-API-Key": TEST_API_KEY, "Cookie": f"relab_session={TEST_COOKIE_VALUE}"},
        )

        await limiter.handle(request, lambda _request: _response())

        stored_keys = set(limiter._attempts)
        assert stored_keys
        assert all(TEST_API_KEY not in key for key in stored_keys)
        assert all(TEST_COOKIE_VALUE not in key for key in stored_keys)
