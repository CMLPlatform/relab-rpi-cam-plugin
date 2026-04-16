"""Tests for setup page endpoints."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.api.routers import setup as setup_router
from app.core.config import DEFAULT_PAIRING_BACKEND_URL, settings
from app.core.runtime import AppRuntime
from tests.constants import EXAMPLE_RELAY_BACKEND_URL, HTML_CONTENT_TYPE
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
PAIRING_CODE = "ABC123"
PAIRED_TEXT = "Paired"
PAIRING_FAILED_TEXT = "Pairing failed"
PAIRED_SUCCESS_TEXT = "Connecting now."
THIS_IP_PLACEHOLDER = "&lt;this-ip&gt;"
COPY_PAIRING_CODE_LABEL = "Copy pairing code"
NEW_PAIRING_CODE_LABEL = "Generate a new pairing code"
LATENCY_BOOST_TEXT = "Native RELab app latency boost"
STANDALONE_CLIENTS_TEXT = "Browser and script access"
LOCAL_KEY_WARNING_TEXT = "Relay pairing still uses the 6-character code above."
LOCAL_KEY_NOTE_TEXT = "Direct LAN access for browser, app, and scripts."
LOCAL_API_KEY_TEXT = "Local API key"
HLS_PREVIEW_TEXT = "HLS preview"
PREVIEW_HLS_URL = f"http://{THIS_IP_PLACEHOLDER}:8018/preview/hls/cam-preview/index.m3u8"
API_TEXT = "API"
PAIRING_EXPIRY_ATTR = "data-pairing-expiry"
PAIRING_TTL_ATTR = 'data-ttl-ms="600000"'
UNPAIR_FUNCTION_CALL = "unpair()"
PAIRING_REFRESH_HINT_TEXT = "Refresh after pairing or unpairing to see the latest state."
SETUP_ADVANCED_OPEN = '<details class="setup-advanced" open'
SETUP_PAIRING_INSTRUCTION = "Enter this code in ReLab."
SETUP_NO_RELAY_RETRY = "No relay yet. Pairing keeps retrying."
SETUP_OPEN_HINT = "click to open"
SETUP_LOCAL_DNS_SUFFIX = ".local"
PAIRING_TASK_NAME = "pairing"
HTTP_EQUIV_REFRESH = 'http-equiv="refresh"'
HEADER_FRAME_OPTIONS = "DENY"
HEADER_NOSNIFF = "nosniff"
HEADER_NO_REFERRER = "no-referrer"
SETUP_CSP_DEFAULT = "default-src 'self'"
SETUP_CSP_INLINE = "'unsafe-inline'"
THEME_TOGGLE_MARKER = "data-theme-toggle"
LOGO_SRC = "/static/logo.png"
THEME_AUTO_LABEL = "Theme: Auto"


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
        monkeypatch.setattr(setup_router, "_pairing_backend_reachable", AsyncMock(return_value=True))
        monkeypatch.setattr(setup_router, "_get_candidate_urls", list)
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
        assert resp.headers["x-frame-options"] == HEADER_FRAME_OPTIONS
        assert resp.headers["x-content-type-options"] == HEADER_NOSNIFF
        assert resp.headers["referrer-policy"] == HEADER_NO_REFERRER
        assert SETUP_CSP_DEFAULT in resp.headers["content-security-policy"]
        assert SETUP_CSP_INLINE in resp.headers["content-security-policy"]

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

    async def test_setup_page_shows_pairing_url_and_change_hints_when_backend_is_reachable(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """The setup page should always show the configured pairing URL and how to change it."""
        resp = await unauthed_client.get("/setup")
        assert PAIRING_URL_DEBUG_TEXT in resp.text
        assert PAIRING_BACKEND_URL_TEXT in resp.text
        assert DEFAULT_BACKEND_PRESET_TEXT in resp.text
        assert CHANGE_IT_TEXT in resp.text
        assert PAIRING_BACKEND_URL_VALUE in resp.text
        assert REACHABLE_TEXT in resp.text
        assert NOT_REACHABLE_TEXT not in resp.text
        assert SETUP_OPEN_HINT in resp.text

    async def test_setup_page_shows_pairing_help_when_backend_is_unreachable(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The debugging section should reflect when the backend cannot be reached."""
        monkeypatch.setattr(setup_router, "_pairing_backend_reachable", AsyncMock(return_value=False))
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
        assert NEW_PAIRING_CODE_LABEL in resp.text
        assert PAIRED_TEXT not in resp.text

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

    async def test_setup_page_shows_unpair_button_when_relay_enabled(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Unpair button is visible when relay credentials are configured."""
        self._runtime.runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme="device_assertion",
            relay_key_id="key-1",
            relay_private_key_pem="pem",
        )
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert UNPAIR_FUNCTION_CALL in resp.text
        assert PAIRING_BACKEND_URL_TEXT in resp.text

    async def test_setup_page_keeps_local_access_collapsed_by_default(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Local access should not open by default."""
        self._runtime.runtime_state.set_local_api_key("test-local-api-key")
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert SETUP_ADVANCED_OPEN not in resp.text
        assert LATENCY_BOOST_TEXT in resp.text
        assert STANDALONE_CLIENTS_TEXT in resp.text
        assert LOCAL_KEY_WARNING_TEXT in resp.text
        assert LOCAL_KEY_NOTE_TEXT in resp.text

    async def test_setup_page_renders_local_access_as_compact_value_cards(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Local access values should render in dedicated cards without raw line-break layout."""
        self._runtime.runtime_state.set_local_api_key("test-local-api-key")
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert LOCAL_API_KEY_TEXT in resp.text
        assert HLS_PREVIEW_TEXT in resp.text
        assert PREVIEW_HLS_URL in resp.text
        assert API_TEXT in resp.text
        assert 'class="setup-value-list"' in resp.text
        assert 'class="setup-value-card__content setup-value-card__content--secret"' in resp.text

    async def test_setup_page_falls_back_to_this_ip_placeholder_when_no_mdns_name(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """The direct-connect instructions should not claim a bogus LAN IP."""
        self._runtime.runtime_state.set_local_api_key("test-local-api-key")
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert THIS_IP_PLACEHOLDER in resp.text
        assert SETUP_LOCAL_DNS_SUFFIX not in resp.text


class TestUnpair:
    """Tests for DELETE /pairing."""

    async def test_unpair_returns_204(self, unauthed_client: AsyncClient) -> None:
        """Endpoint returns 204 No Content immediately."""
        with (
            patch("app.api.routers.setup.delete_relay_credentials"),
            patch("app.api.routers.setup.clear_runtime_relay_credentials"),
            patch("app.api.routers.setup.asyncio.sleep"),
        ):
            resp = await unauthed_client.delete("/pairing")
        assert resp.status_code == 204

    async def test_unpair_deletes_credentials_and_clears_settings(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Credentials file is deleted and runtime settings are cleared after the brief delay."""
        deleted: list[bool] = []
        cleared: list[bool] = []
        pairing_service = FakePairingService(auto_pair=False)
        runtime = SpyRuntime()
        runtime.relay_service = FakeRelayService(runtime)
        runtime.pairing_service = pairing_service

        with (
            patch("app.api.routers.setup.delete_relay_credentials", side_effect=lambda: deleted.append(True)),
            patch(
                "app.api.routers.setup.clear_runtime_relay_credentials",
                side_effect=lambda _runtime_state: cleared.append(True),
            ),
            patch("app.api.routers.setup.asyncio.sleep"),  # skip the 0.1s delay
            patch(
                "app.api.routers.setup.get_request_runtime",
                return_value=runtime,
            ),
        ):
            resp = await unauthed_client.delete("/pairing")
            await runtime.created_tasks[0]

        assert resp.status_code == 204
        assert deleted == [True]
        assert cleared == [True]


class TestPairingCodeRefresh:
    """Tests for POST /pairing/code."""

    async def test_refresh_returns_204(self, unauthed_client: AsyncClient) -> None:
        """Endpoint returns 204 No Content immediately."""
        with patch("app.api.routers.setup.asyncio.sleep"):
            resp = await unauthed_client.post("/pairing/code")
        assert resp.status_code == 204

    async def test_refresh_restarts_pairing_without_deleting_credentials(
        self,
        unauthed_client: AsyncClient,
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
            patch("app.api.routers.setup.asyncio.sleep"),
            patch(
                "app.api.routers.setup.get_request_runtime",
                return_value=runtime,
            ),
        ):
            resp = await unauthed_client.post("/pairing/code")
            await runtime.created_tasks[0]

        assert resp.status_code == 204
        assert reset_called == [True]
        assert any(task.get_name() == PAIRING_TASK_NAME for task in runtime.created_tasks)
