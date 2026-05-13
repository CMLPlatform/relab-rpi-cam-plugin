"""Tests for setup page endpoints."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Self
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from pydantic import HttpUrl

from app.auth.dependencies import create_session
from app.core.runtime import AppRuntime
from app.core.settings import DEFAULT_PAIRING_BACKEND_URL, settings
from app.pairing.routers import setup as setup_router
from tests.constants import (
    COOP_SAME_ORIGIN,
    CORP_SAME_ORIGIN,
    CSP_NONCE_ATTR_RE,
    EXAMPLE_RELAY_BACKEND_URL,
    HEADER_COOP,
    HEADER_CORP,
    HEADER_CSP,
    HTML_CONTENT_TYPE,
)
from tests.support.fakes import FakePairingService, FakeRelayService, SpyRuntime

SETUP_TITLE = "RPi Camera — Setup"
SETUP_COPY_TEXT = "Pair, refresh, or unpair from this page."
PAIRING_URL_DEBUG_TEXT = "Pairing details"
PAIRING_BACKEND_URL_TEXT = "URL"
DEFAULT_BACKEND_PRESET_TEXT = "Default RELab backend"
PAIRING_BACKEND_URL_VALUE = "https://api.cml-relab.org"
REACHABLE_TEXT = "Reachable"
NOT_REACHABLE_TEXT = "Not reachable"
HOW_TO_CHANGE_TEXT = "Change"
CHANGE_IT_TEXT = "Change"
PAIRING_CODE = "ABC234"
EXAMPLE_RELAY_CAMERA_ID = "cam-1"
PAIRED_TEXT = "Paired"
PAIRING_FAILED_TEXT = "Pairing failed"
PAIRED_SUCCESS_TEXT = "Connecting now."
THIS_IP_PLACEHOLDER = "&lt;this-ip&gt;"
COPY_PAIRING_CODE_LABEL = "Copy pairing code"
NEW_PAIRING_CODE_LABEL = "Generate a new pairing code"
NEW_PAIRING_CODE_BUTTON = 'class="setup-refresh-code-btn"'
UNPAIR_BUTTON_MARKER = "data-unpair-open"
LATENCY_BOOST_TEXT = "Native RELab app latency boost"
STANDALONE_CLIENTS_TEXT = "Browser and script access"
LOCAL_KEY_WARNING_TEXT = "Relay pairing still uses the 6-character unambiguous code above."
LOCAL_KEY_NOTE_TEXT = "Direct LAN access for browser, app, and scripts."
LOCAL_API_KEY_TEXT = "Local API key"
REVEAL_LOCAL_KEY_TEXT = "Reveal local API key"
LOCAL_API_KEY_PLACEHOLDER = "Key hidden until revealed."
SETUP_LOCAL_API_KEY_VALUE = "test-local-api-key"
HLS_PREVIEW_TEXT = "HLS preview"
PREVIEW_HLS_URL = f"http://{THIS_IP_PLACEHOLDER}:8018/preview/hls/cam-preview/index.m3u8"
API_TEXT = "API"
PAIRING_EXPIRY_ATTR = "data-pairing-expiry"
PAIRING_TTL_ATTR = 'data-ttl-ms="600000"'
PAIRING_REFRESH_HINT_TEXT = "Refresh after pairing or unpairing to see the latest state."
SETUP_ADVANCED_OPEN = '<details class="setup-advanced" open'
SETUP_PAIRING_INSTRUCTION = "Enter this code in ReLab."
SETUP_NO_RELAY_RETRY = "No relay yet. Pairing keeps retrying."
SETUP_OPEN_HINT = "click to open"
SETUP_LOCAL_DNS_SUFFIX = ".local"
PAIRING_TASK_NAME = "pairing"
HTTP_EQUIV_REFRESH = 'http-equiv="refresh"'
HEADER_FRAME_OPTIONS = "x-frame-options"
HEADER_HSTS = "strict-transport-security"
HEADER_NOSNIFF = "nosniff"
HEADER_NO_REFERRER = "no-referrer"
HSTS_WITH_SUBDOMAINS = "max-age=31536000; includeSubDomains"
HSTS_INCLUDE_SUBDOMAINS = "includeSubDomains"
HSTS_PRELOAD = "preload"
SETUP_CSP_DEFAULT = "default-src 'self'"
SETUP_CSP_INLINE = "'unsafe-inline'"
SETUP_CSP_CDN = "https://cdn.jsdelivr.net"
SETUP_CSP_OBJECTS_BLOCKED = "object-src 'none'"
THEME_TOGGLE_MARKER = "data-theme-toggle"
LOGO_SRC = "/static/logo.png"
THEME_INIT_JS_SRC = "/static/theme-init.js"
SETUP_PAGE_JS_SRC = "/static/setup-page.js"
LOGOUT_FORM_MARKER = 'class="site-header__logout"'
THEME_AUTO_LABEL = "Theme: Auto"


class TestPairingBackendReachability:
    """Tests for setup-page pairing backend probes."""

    async def test_pairing_backend_probe_does_not_follow_redirects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The external reachability probe should not follow backend redirects."""
        seen_kwargs: dict[str, object] = {}
        get = AsyncMock()

        class FakeClient:
            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def get(self, *_args: object, **_kwargs: object) -> None:
                await get()

        def fake_async_client(*_args: object, **kwargs: object) -> FakeClient:
            seen_kwargs.update(kwargs)
            return FakeClient()

        monkeypatch.setattr(settings, "pairing_backend_url", "https://api.example")
        monkeypatch.setattr(setup_router.httpx, "AsyncClient", fake_async_client)

        assert await setup_router._pairing_backend_reachable() is True
        assert seen_kwargs == {
            "timeout": setup_router._PAIRING_BACKEND_REACHABILITY_TIMEOUT,
            "follow_redirects": False,
        }
        get.assert_awaited_once()


class TestSetupPage:
    """Tests for GET /setup (no auth required)."""

    @pytest.fixture(autouse=True)
    def _default_pairing_backend_reachability(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Keep setup-page tests deterministic without making real network calls."""
        runtime = AppRuntime()
        runtime.pairing_service.state.status = "idle"
        runtime.pairing_service.state.code = None
        runtime.pairing_service.state.error = None
        runtime.pairing_service.state.expires_at = None
        self._pairing_backend_reachable = AsyncMock(return_value=True)
        self._get_candidate_urls = MagicMock(side_effect=list)
        monkeypatch.setattr(setup_router, "_pairing_backend_reachable", self._pairing_backend_reachable)
        monkeypatch.setattr(setup_router, "_get_candidate_urls", self._get_candidate_urls)
        monkeypatch.setattr(setup_router, "get_request_runtime", lambda _request: runtime)
        self._runtime = runtime
        self._pairing_state = runtime.pairing_service.state
        monkeypatch.setattr(settings, "pairing_backend_url", DEFAULT_PAIRING_BACKEND_URL)
        monkeypatch.setattr(settings, "relay_backend_url", "")
        monkeypatch.setattr(settings, "relay_camera_id", "")
        monkeypatch.setattr(settings, "relay_key_id", "")
        monkeypatch.setattr(settings, "relay_private_key_pem", "")

    async def test_setup_page_returns_html(self, unauthed_client: AsyncClient) -> None:
        """Test that the setup page returns HTML."""
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert HTML_CONTENT_TYPE in resp.headers["content-type"]
        assert HTTP_EQUIV_REFRESH not in resp.text

    async def test_setup_page_sets_security_headers(self, unauthed_client: AsyncClient) -> None:
        """Setup page responses should carry the hardened browser headers."""
        resp = await unauthed_client.get("/setup")
        assert HEADER_FRAME_OPTIONS not in resp.headers
        assert HEADER_HSTS not in resp.headers
        assert resp.headers["x-content-type-options"] == HEADER_NOSNIFF
        assert resp.headers["referrer-policy"] == HEADER_NO_REFERRER
        assert resp.headers[HEADER_COOP] == COOP_SAME_ORIGIN
        assert resp.headers[HEADER_CORP] == CORP_SAME_ORIGIN
        assert SETUP_CSP_DEFAULT in resp.headers[HEADER_CSP]
        assert SETUP_CSP_INLINE not in resp.headers[HEADER_CSP]
        assert SETUP_CSP_CDN not in resp.headers[HEADER_CSP]
        assert SETUP_CSP_OBJECTS_BLOCKED in resp.headers[HEADER_CSP]

    async def test_setup_page_scripts_use_matching_csp_nonce(self, unauthed_client: AsyncClient) -> None:
        """Server-rendered setup scripts should use the nonce advertised in CSP."""
        resp = await unauthed_client.get("/setup")
        nonce_values = set(CSP_NONCE_ATTR_RE.findall(resp.text))

        assert nonce_values
        assert len(nonce_values) == 1
        nonce = next(iter(nonce_values))
        assert f"'nonce-{nonce}'" in resp.headers[HEADER_CSP]

    async def test_setup_page_sets_host_only_hsts_for_https_base_url(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTPS deployments should get HSTS with subdomain coverage, without preload commitment."""
        monkeypatch.setattr(settings, "base_url", HttpUrl("https://camera.example/"))

        resp = await unauthed_client.get("/setup")

        assert resp.headers[HEADER_HSTS] == HSTS_WITH_SUBDOMAINS
        assert HSTS_INCLUDE_SUBDOMAINS in resp.headers[HEADER_HSTS]
        assert HSTS_PRELOAD not in resp.headers[HEADER_HSTS]

    async def test_setup_page_contains_title(self, unauthed_client: AsyncClient) -> None:
        """Test that the setup page contains the correct title."""
        resp = await unauthed_client.get("/setup")
        assert SETUP_TITLE in resp.text

    async def test_setup_page_contains_pairing_copy(self, unauthed_client: AsyncClient) -> None:
        """Test that the setup page shows the pairing instructions."""
        resp = await unauthed_client.get("/setup")
        assert SETUP_COPY_TEXT in resp.text

    async def test_setup_page_renders_shared_header_and_theme_control(self, unauthed_client: AsyncClient) -> None:
        """The setup page should expose the shared site header affordances."""
        resp = await unauthed_client.get("/setup")
        assert THEME_TOGGLE_MARKER in resp.text
        assert THEME_AUTO_LABEL in resp.text
        assert LOGO_SRC in resp.text
        assert THEME_INIT_JS_SRC in resp.text
        assert SETUP_PAGE_JS_SRC in resp.text

    async def test_setup_page_hides_pairing_url_and_change_hints_from_public_visitors(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Public setup visitors should not see backend topology details."""
        resp = await unauthed_client.get("/setup")
        assert PAIRING_URL_DEBUG_TEXT not in resp.text
        assert PAIRING_BACKEND_URL_TEXT not in resp.text
        assert DEFAULT_BACKEND_PRESET_TEXT not in resp.text
        assert CHANGE_IT_TEXT not in resp.text
        assert PAIRING_BACKEND_URL_VALUE not in resp.text
        assert REACHABLE_TEXT not in resp.text
        assert NOT_REACHABLE_TEXT not in resp.text
        assert SETUP_OPEN_HINT not in resp.text
        self._pairing_backend_reachable.assert_not_awaited()
        self._get_candidate_urls.assert_not_called()

    async def test_setup_page_shows_pairing_url_and_change_hints_to_browser_sessions(
        self,
        client: AsyncClient,
    ) -> None:
        """Authenticated operators should see backend topology and change hints."""
        client.cookies.set(settings.browser_session_cookie_name, create_session())
        resp = await client.get("/setup")
        assert PAIRING_URL_DEBUG_TEXT in resp.text
        assert PAIRING_BACKEND_URL_TEXT in resp.text
        assert DEFAULT_BACKEND_PRESET_TEXT in resp.text
        assert CHANGE_IT_TEXT in resp.text
        assert PAIRING_BACKEND_URL_VALUE in resp.text
        assert REACHABLE_TEXT in resp.text
        assert SETUP_OPEN_HINT in resp.text

    async def test_setup_page_shows_pairing_help_when_backend_is_unreachable(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The operator debugging section should reflect when the backend cannot be reached."""
        monkeypatch.setattr(setup_router, "_pairing_backend_reachable", AsyncMock(return_value=False))
        unauthed_client.cookies.set(settings.browser_session_cookie_name, create_session())
        resp = await unauthed_client.get("/setup")
        assert NOT_REACHABLE_TEXT in resp.text
        assert SETUP_NO_RELAY_RETRY in resp.text

    async def test_setup_page_shows_pairing_status(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Test that the setup page shows the pairing code while waiting for relay confirmation."""
        self._pairing_state.status = "waiting"
        self._pairing_state.code = PAIRING_CODE
        self._pairing_state.error = None
        self._pairing_state.expires_at = datetime.now(UTC) + timedelta(minutes=10)
        resp = await unauthed_client.get("/setup")
        assert PAIRING_CODE in resp.text
        assert PAIRING_EXPIRY_ATTR in resp.text
        assert PAIRING_TTL_ATTR in resp.text
        assert SETUP_PAIRING_INSTRUCTION in resp.text
        assert PAIRING_REFRESH_HINT_TEXT in resp.text
        assert COPY_PAIRING_CODE_LABEL in resp.text
        assert NEW_PAIRING_CODE_BUTTON not in resp.text
        assert PAIRED_TEXT not in resp.text

    async def test_setup_page_shows_pairing_refresh_to_browser_sessions(
        self,
        client: AsyncClient,
    ) -> None:
        """Authenticated setup visitors should see the pairing-code refresh action."""
        self._pairing_state.status = "waiting"
        self._pairing_state.code = PAIRING_CODE
        self._pairing_state.error = None
        self._pairing_state.expires_at = datetime.now(UTC) + timedelta(minutes=10)
        client.cookies.set(settings.browser_session_cookie_name, create_session())
        resp = await client.get("/setup")
        assert resp.status_code == 200
        assert COPY_PAIRING_CODE_LABEL in resp.text
        assert NEW_PAIRING_CODE_BUTTON in resp.text
        assert NEW_PAIRING_CODE_LABEL in resp.text
        assert LOGOUT_FORM_MARKER in resp.text

    async def test_setup_page_shows_pairing_error(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Test that an error message is shown on the setup page when pairing fails."""
        self._pairing_state.status = "error"
        self._pairing_state.code = None
        self._pairing_state.error = PAIRING_FAILED_TEXT
        self._pairing_state.expires_at = None
        resp = await unauthed_client.get("/setup")
        assert PAIRING_FAILED_TEXT in resp.text

    async def test_setup_page_shows_paired_status(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Test that the setup page shows the paired state after a successful pairing."""
        self._pairing_state.status = "paired"
        self._pairing_state.code = None
        self._pairing_state.error = None
        self._pairing_state.expires_at = None
        resp = await unauthed_client.get("/setup")
        assert PAIRED_TEXT in resp.text
        assert PAIRED_SUCCESS_TEXT in resp.text

    async def test_setup_page_hides_unpair_button_from_unauthenticated_visitors(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Public setup visibility should not expose actions that require auth."""
        self._runtime.runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id=EXAMPLE_RELAY_CAMERA_ID,
            relay_auth_scheme="device_assertion",
            relay_key_id="key-1",
            relay_private_key_pem="pem",
        )
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert UNPAIR_BUTTON_MARKER not in resp.text
        assert LOGOUT_FORM_MARKER not in resp.text
        assert PAIRING_BACKEND_URL_TEXT not in resp.text
        assert EXAMPLE_RELAY_BACKEND_URL not in resp.text
        assert EXAMPLE_RELAY_CAMERA_ID not in resp.text

    async def test_setup_page_shows_unpair_button_to_browser_sessions(
        self,
        client: AsyncClient,
    ) -> None:
        """Authenticated setup visitors should see the unpair action when relay credentials are configured."""
        self._runtime.runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id=EXAMPLE_RELAY_CAMERA_ID,
            relay_auth_scheme="device_assertion",
            relay_key_id="key-1",
            relay_private_key_pem="pem",
        )
        client.cookies.set(settings.browser_session_cookie_name, create_session())
        resp = await client.get("/setup")
        assert resp.status_code == 200
        assert UNPAIR_BUTTON_MARKER in resp.text
        assert PAIRING_BACKEND_URL_TEXT in resp.text

    async def test_setup_page_keeps_local_access_collapsed_by_default(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Public setup visitors should not see direct local-access details."""
        self._runtime.runtime_state.set_local_api_key("test-local-api-key")
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert SETUP_ADVANCED_OPEN not in resp.text
        assert LATENCY_BOOST_TEXT not in resp.text
        assert STANDALONE_CLIENTS_TEXT not in resp.text
        assert LOCAL_KEY_WARNING_TEXT not in resp.text
        assert LOCAL_KEY_NOTE_TEXT not in resp.text

    async def test_setup_page_renders_local_access_as_compact_value_cards(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Unauthenticated setup visitors should not see the local API key."""
        self._runtime.runtime_state.set_local_api_key(SETUP_LOCAL_API_KEY_VALUE)
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert LOCAL_API_KEY_TEXT not in resp.text
        assert SETUP_LOCAL_API_KEY_VALUE not in resp.text

    async def test_setup_page_shows_local_api_key_reveal_to_browser_sessions(
        self,
        client: AsyncClient,
    ) -> None:
        """The setup page should require an explicit reveal before fetching the local API key."""
        self._runtime.runtime_state.set_local_api_key(SETUP_LOCAL_API_KEY_VALUE)
        client.cookies.set(settings.browser_session_cookie_name, create_session())
        resp = await client.get("/setup")
        assert resp.status_code == 200
        assert LOCAL_API_KEY_TEXT in resp.text
        assert REVEAL_LOCAL_KEY_TEXT in resp.text
        assert LOCAL_API_KEY_PLACEHOLDER in resp.text
        assert SETUP_LOCAL_API_KEY_VALUE not in resp.text
        assert HLS_PREVIEW_TEXT in resp.text
        assert PREVIEW_HLS_URL in resp.text
        assert API_TEXT in resp.text

    async def test_setup_page_falls_back_to_this_ip_placeholder_when_no_mdns_name(
        self,
        client: AsyncClient,
    ) -> None:
        """The direct-connect instructions should not claim a bogus LAN IP."""
        self._runtime.runtime_state.set_local_api_key("test-local-api-key")
        client.cookies.set(settings.browser_session_cookie_name, create_session())
        resp = await client.get("/setup")
        assert resp.status_code == 200
        assert THIS_IP_PLACEHOLDER in resp.text
        assert SETUP_LOCAL_DNS_SUFFIX not in resp.text


class TestPairingState:
    """Tests for GET /pairing/state."""

    @pytest.fixture(autouse=True)
    def _patch_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runtime = AppRuntime()
        runtime.pairing_service.state.status = "waiting"
        monkeypatch.setattr(setup_router, "get_request_runtime", lambda _request: runtime)
        self._runtime = runtime

    async def test_returns_current_state(self, unauthed_client: AsyncClient) -> None:
        """Endpoint should return the current pairing state from the runtime."""
        resp = await unauthed_client.get("/pairing/state")
        assert resp.status_code == 200
        assert resp.json() == {"status": "waiting", "relay_enabled": False}

    async def test_reflects_paired_state(self, unauthed_client: AsyncClient) -> None:
        """When the pairing service reports paired, the endpoint should reflect that."""
        self._runtime.pairing_service.state.status = "paired"
        self._runtime.runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="00000000-0000-0000-0000-000000000001",
            relay_auth_scheme="device_assertion",
            relay_key_id="kid-1",
            relay_private_key_pem="pem",
        )
        resp = await unauthed_client.get("/pairing/state")
        assert resp.json() == {"status": "paired", "relay_enabled": True}


class TestUnpair:
    """Tests for DELETE /pairing."""

    async def test_unpair_rejects_unauthenticated_requests(self, unauthed_client: AsyncClient) -> None:
        """Local network access alone should not allow unpairing."""
        resp = await unauthed_client.delete("/pairing")
        assert resp.status_code == 401

    async def test_unpair_returns_204(self, client: AsyncClient) -> None:
        """Endpoint returns 204 No Content immediately."""
        with (
            patch("app.pairing.routers.setup.delete_relay_credentials"),
            patch("app.pairing.routers.setup.clear_runtime_relay_credentials"),
            patch("app.pairing.routers.setup.asyncio.sleep"),
        ):
            resp = await client.delete("/pairing")
        assert resp.status_code == 204

    async def test_unpair_deletes_credentials_and_clears_settings(
        self,
        client: AsyncClient,
    ) -> None:
        """Credentials file is deleted and runtime settings are cleared after the brief delay."""
        deleted: list[bool] = []
        cleared: list[bool] = []
        pairing_service = FakePairingService(auto_pair=False)
        runtime = SpyRuntime()
        runtime.relay_service = FakeRelayService(runtime)
        runtime.pairing_service = pairing_service

        with (
            patch("app.pairing.routers.setup.delete_relay_credentials", side_effect=lambda: deleted.append(True)),
            patch(
                "app.pairing.routers.setup.clear_runtime_relay_credentials",
                side_effect=lambda _runtime_state: cleared.append(True),
            ),
            patch("app.pairing.routers.setup.asyncio.sleep"),  # skip the 0.1s delay
            patch(
                "app.pairing.routers.setup.get_request_runtime",
                return_value=runtime,
            ),
        ):
            resp = await client.delete("/pairing")
            await asyncio.wait_for(runtime.created_tasks[0], timeout=1)

        assert resp.status_code == 204
        assert deleted == [True]
        assert cleared == [True]


class TestPairingCodeRefresh:
    """Tests for POST /pairing/code."""

    async def test_refresh_rejects_unauthenticated_requests(self, unauthed_client: AsyncClient) -> None:
        """Local network access alone should not allow pairing-code rotation."""
        resp = await unauthed_client.post("/pairing/code")
        assert resp.status_code == 401

    async def test_refresh_returns_204(self, client: AsyncClient) -> None:
        """Endpoint returns 204 No Content immediately."""
        with patch("app.pairing.routers.setup.asyncio.sleep"):
            resp = await client.post("/pairing/code")
        assert resp.status_code == 204

    async def test_refresh_restarts_pairing_without_deleting_credentials(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Refreshing the code should restart pairing without touching credentials."""
        reset_called: list[bool] = []
        monkeypatch.setattr(settings, "pairing_backend_url", DEFAULT_PAIRING_BACKEND_URL)
        monkeypatch.setattr(settings, "relay_backend_url", "")
        monkeypatch.setattr(settings, "relay_camera_id", "")
        monkeypatch.setattr(settings, "relay_key_id", "")
        monkeypatch.setattr(settings, "relay_private_key_pem", "")

        class ResetTrackingPairingService(FakePairingService):
            def reset_state(self) -> None:
                super().reset_state()
                reset_called.append(True)

        pairing_service = ResetTrackingPairingService(auto_pair=False)
        runtime = SpyRuntime()
        runtime.relay_service = FakeRelayService(runtime)
        runtime.pairing_service = pairing_service

        with (
            patch("app.pairing.routers.setup.asyncio.sleep"),
            patch(
                "app.pairing.routers.setup.get_request_runtime",
                return_value=runtime,
            ),
        ):
            resp = await client.post("/pairing/code")
            await asyncio.wait_for(runtime.created_tasks[0], timeout=1)

        assert resp.status_code == 204
        assert reset_called == [True]
        assert any(task.get_name() == PAIRING_TASK_NAME for task in runtime.created_tasks)
