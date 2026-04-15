"""Tests for setup page endpoints."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.api.routers import setup as setup_router
from app.core.config import settings
from tests.constants import EXAMPLE_RELAY_BACKEND_URL, HTML_CONTENT_TYPE

SETUP_TITLE = "RPi Camera — Setup"
SETUP_COPY_TEXT = "Pair this camera with the ReLab app"
PAIRING_CODE = "ABC123"
RELAY_CONNECTED_TEXT = "Relay configured"
PAIRING_FAILED_TEXT = "Pairing failed"
PAIRED_SUCCESS_TEXT = "Pairing complete"
PAIRING_EXPIRY_ATTR = "data-pairing-expiry"
PAIRING_TTL_ATTR = 'data-ttl-ms="600000"'
UNPAIR_FUNCTION_CALL = "unpair()"


class TestSetupPage:
    """Tests for GET /setup (no auth required)."""

    async def test_setup_page_returns_html(self, unauthed_client: AsyncClient) -> None:
        """Test that the setup page returns HTML."""
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert HTML_CONTENT_TYPE in resp.headers["content-type"]

    async def test_setup_page_contains_title(self, unauthed_client: AsyncClient) -> None:
        """Test that the setup page contains the correct title."""
        resp = await unauthed_client.get("/setup")
        assert SETUP_TITLE in resp.text

    async def test_setup_page_contains_pairing_copy(self, unauthed_client: AsyncClient) -> None:
        """Test that the setup page shows the pairing instructions."""
        resp = await unauthed_client.get("/setup")
        assert SETUP_COPY_TEXT in resp.text

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
        original = (
            settings.relay_backend_url,
            settings.relay_camera_id,
            settings.relay_key_id,
            settings.relay_private_key_pem,
        )
        settings.relay_backend_url = EXAMPLE_RELAY_BACKEND_URL
        settings.relay_camera_id = "cam-1"
        settings.relay_key_id = "key-1"
        settings.relay_private_key_pem = "private-key"
        try:
            resp = await unauthed_client.get("/setup")
            assert PAIRING_CODE in resp.text
            assert PAIRING_EXPIRY_ATTR in resp.text
            assert PAIRING_TTL_ATTR in resp.text
            assert RELAY_CONNECTED_TEXT in resp.text
        finally:
            (
                settings.relay_backend_url,
                settings.relay_camera_id,
                settings.relay_key_id,
                settings.relay_private_key_pem,
            ) = original

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
            await asyncio.sleep(0)  # let the background task run
        assert resp.status_code == 204

    async def test_unpair_deletes_credentials_and_clears_settings(
        self,
        unauthed_client: AsyncClient,
    ) -> None:
        """Credentials file is deleted and runtime settings are cleared after the brief delay."""
        deleted: list[bool] = []
        cleared: list[bool] = []

        with (
            patch("app.api.routers.setup.delete_relay_credentials", side_effect=lambda: deleted.append(True)),
            patch("app.api.routers.setup.clear_runtime_relay_credentials", side_effect=lambda: cleared.append(True)),
            patch("app.api.routers.setup.asyncio.sleep"),  # skip the 0.1s delay
        ):
            resp = await unauthed_client.delete("/pairing/credentials")
            await asyncio.sleep(0)  # yield so the background task executes

        assert resp.status_code == 204
        assert deleted == [True]
        assert cleared == [True]
