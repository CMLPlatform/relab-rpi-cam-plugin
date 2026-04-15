"""Tests for setup page endpoints."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.api.routers import setup as setup_router
from app.core.config import DEFAULT_PAIRING_BACKEND_URL, settings
from tests.constants import EXAMPLE_RELAY_BACKEND_URL, HTML_CONTENT_TYPE

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
LOCAL_KEY_NOTE_TEXT = "The local API works in browsers on your LAN, in the native RELab app, and in custom scripts."
PAIRING_EXPIRY_ATTR = "data-pairing-expiry"
PAIRING_TTL_ATTR = 'data-ttl-ms="600000"'
UNPAIR_FUNCTION_CALL = "unpair()"
PAIRING_REFRESH_HINT_TEXT = "Refresh after pairing or unpairing to see the latest state."


class TestSetupPage:
    """Tests for GET /setup (no auth required)."""

    @pytest.fixture(autouse=True)
    def _default_pairing_backend_reachability(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Keep setup-page tests deterministic without making real network calls."""
        monkeypatch.setattr(setup_router, "_pairing_backend_reachable", AsyncMock(return_value=True))
        monkeypatch.setattr(setup_router, "_get_candidate_urls", list)
        monkeypatch.setattr(
            setup_router,
            "get_pairing_state",
            lambda: SimpleNamespace(status="idle", code=None, error=None, expires_at=None),
        )
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
        assert 'http-equiv="refresh"' not in resp.text

    async def test_setup_page_contains_title(self, unauthed_client: AsyncClient) -> None:
        """Test that the setup page contains the correct title."""
        resp = await unauthed_client.get("/setup")
        assert SETUP_TITLE in resp.text

    async def test_setup_page_contains_pairing_copy(self, unauthed_client: AsyncClient) -> None:
        """Test that the setup page shows the pairing instructions."""
        resp = await unauthed_client.get("/setup")
        assert SETUP_COPY_TEXT in resp.text

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
        assert "click to open" in resp.text

    async def test_setup_page_shows_pairing_help_when_backend_is_unreachable(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The debugging section should reflect when the backend cannot be reached."""
        monkeypatch.setattr(setup_router, "_pairing_backend_reachable", AsyncMock(return_value=False))
        resp = await unauthed_client.get("/setup")
        assert NOT_REACHABLE_TEXT in resp.text
        assert "No relay yet. Pairing keeps retrying." in resp.text

    async def test_setup_page_shows_pairing_status(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that the setup page shows the pairing code while waiting for relay confirmation."""
        monkeypatch.setattr(
            setup_router,
            "get_pairing_state",
            lambda: SimpleNamespace(
                status="waiting",
                code=PAIRING_CODE,
                error=None,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            ),
        )
        resp = await unauthed_client.get("/setup")
        assert PAIRING_CODE in resp.text
        assert PAIRING_EXPIRY_ATTR in resp.text
        assert PAIRING_TTL_ATTR in resp.text
        assert "Enter this code in ReLab." in resp.text
        assert PAIRING_REFRESH_HINT_TEXT in resp.text
        assert COPY_PAIRING_CODE_LABEL in resp.text
        assert NEW_PAIRING_CODE_LABEL in resp.text
        assert PAIRED_TEXT not in resp.text

    async def test_setup_page_shows_pairing_error(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that an error message is shown on the setup page when pairing fails."""
        monkeypatch.setattr(
            setup_router,
            "get_pairing_state",
            lambda: SimpleNamespace(status="error", code=None, error=PAIRING_FAILED_TEXT, expires_at=None),
        )
        resp = await unauthed_client.get("/setup")
        assert PAIRING_FAILED_TEXT in resp.text

    async def test_setup_page_shows_paired_status(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that the setup page shows the paired state after a successful pairing."""
        monkeypatch.setattr(
            setup_router,
            "get_pairing_state",
            lambda: SimpleNamespace(status="paired", code=None, error=None, expires_at=None),
        )
        resp = await unauthed_client.get("/setup")
        assert PAIRED_TEXT in resp.text
        assert PAIRED_SUCCESS_TEXT in resp.text

    async def test_setup_page_shows_unpair_button_when_relay_enabled(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unpair button is visible when relay credentials are configured."""
        monkeypatch.setattr(settings, "relay_backend_url", EXAMPLE_RELAY_BACKEND_URL)
        monkeypatch.setattr(settings, "relay_camera_id", "cam-1")
        monkeypatch.setattr(settings, "relay_key_id", "key-1")
        monkeypatch.setattr(settings, "relay_private_key_pem", "pem")
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert UNPAIR_FUNCTION_CALL in resp.text
        assert PAIRING_BACKEND_URL_TEXT in resp.text

    async def test_setup_page_keeps_local_access_collapsed_by_default(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Local access should not open by default."""
        monkeypatch.setattr(settings, "local_api_key", "test-local-api-key")
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert '<details class="setup-advanced" open' not in resp.text
        assert LATENCY_BOOST_TEXT in resp.text
        assert STANDALONE_CLIENTS_TEXT in resp.text
        assert LOCAL_KEY_WARNING_TEXT in resp.text
        assert LOCAL_KEY_NOTE_TEXT in resp.text

    async def test_setup_page_falls_back_to_this_ip_placeholder_when_no_mdns_name(
        self,
        unauthed_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The direct-connect instructions should not claim a bogus LAN IP."""
        monkeypatch.setattr(settings, "local_api_key", "test-local-api-key")
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert THIS_IP_PLACEHOLDER in resp.text
        assert ".local" not in resp.text


class TestUnpair:
    """Tests for DELETE /pairing/credentials."""

    async def test_unpair_returns_204(self, unauthed_client: AsyncClient) -> None:
        """Endpoint returns 204 No Content immediately."""
        with (
            patch("app.api.routers.setup.delete_relay_credentials"),
            patch("app.api.routers.setup.clear_runtime_relay_credentials"),
            patch("app.api.routers.setup.asyncio.sleep"),
        ):
            resp = await unauthed_client.delete("/pairing/credentials")
        assert resp.status_code == 204

    async def test_unpair_deletes_credentials_and_clears_settings(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Credentials file is deleted and runtime settings are cleared after the brief delay."""
        deleted: list[bool] = []
        cleared: list[bool] = []
        created_tasks: list[asyncio.Task[None]] = []

        def _create_task(coro: object, name: str | None = None) -> asyncio.Task[None]:
            task = asyncio.get_running_loop().create_task(coro, name=name)
            created_tasks.append(task)
            return task

        with (
            patch("app.api.routers.setup.delete_relay_credentials", side_effect=lambda: deleted.append(True)),
            patch("app.api.routers.setup.clear_runtime_relay_credentials", side_effect=lambda: cleared.append(True)),
            patch("app.api.routers.setup.asyncio.sleep"),  # skip the 0.1s delay
            patch("app.api.routers.setup.asyncio.create_task", side_effect=_create_task),
        ):
            resp = await unauthed_client.delete("/pairing/credentials")
            await created_tasks[0]

        assert resp.status_code == 204
        assert deleted == [True]
        assert cleared == [True]


class TestPairingCodeRefresh:
    """Tests for POST /pairing/code/refresh."""

    async def test_refresh_returns_204(self, unauthed_client: AsyncClient) -> None:
        """Endpoint returns 204 No Content immediately."""
        with patch("app.api.routers.setup.asyncio.sleep"):
            resp = await unauthed_client.post("/pairing/code/refresh")
        assert resp.status_code == 204

    async def test_refresh_restarts_pairing_without_deleting_credentials(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Refreshing the code should restart pairing without touching credentials."""
        created_tasks: list[asyncio.Task[None]] = []
        reset_called: list[bool] = []

        def _create_task(coro: object, name: str | None = None) -> asyncio.Task[None]:
            task = asyncio.get_running_loop().create_task(coro, name=name)
            created_tasks.append(task)
            return task

        with (
            patch("app.api.routers.setup.reset_pairing_state", side_effect=lambda: reset_called.append(True)),
            patch("app.api.routers.setup.run_pairing", AsyncMock(return_value=None)),
            patch("app.api.routers.setup.asyncio.sleep"),
            patch("app.api.routers.setup.asyncio.create_task", side_effect=_create_task),
        ):
            resp = await unauthed_client.post("/pairing/code/refresh")
            await created_tasks[0]

        assert resp.status_code == 204
        assert reset_called == [True]
        assert any(task.get_name() == "pairing" for task in created_tasks)
