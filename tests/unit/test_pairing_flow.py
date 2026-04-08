"""Tests for the pairing flow helpers."""

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.utils import pairing as pairing_mod

EXAMPLE_BACKEND_URL = "https://example.com"
RELAY_BACKEND_URL = "wss://example.com/ws"
RELAY_CAMERA_ID = "cam-1"
RELAY_API_KEY = "key-1"
PAIRING_CODE_1 = "CODE1"
PAIRING_CODE_2 = "CODE2"


class FakeResponse:
    """Tiny response stub for pairing tests."""

    def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        """Raise an error for non-success statuses."""
        if self.status_code >= 400:
            msg = f"status {self.status_code}"
            raise RuntimeError(msg)

    def json(self) -> dict[str, object]:
        """Return the preset JSON payload."""
        return self._payload


class FakeClient:
    """Async client stub for pairing register/poll requests."""

    def __init__(self, post_responses: list[FakeResponse], get_responses: list[FakeResponse]) -> None:
        self._posts = post_responses
        self._gets = get_responses

    async def post(self, *_: object, **__: object) -> FakeResponse:
        """Return the next queued POST response."""
        return self._posts.pop(0)

    async def get(self, *_: object, **__: object) -> FakeResponse:
        """Return the next queued GET response."""
        return self._gets.pop(0)


class TestRunPairing:
    """Tests for the top-level pairing loop."""

    async def test_noop_when_backend_url_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that pairing exits immediately when the backend URL is unset."""
        monkeypatch.setattr(settings, "pairing_backend_url", "")
        on_paired = AsyncMock()
        await pairing_mod.run_pairing(on_paired)
        on_paired.assert_not_awaited()


class TestPairingCycle:
    """Tests for a single pairing cycle."""

    async def test_retries_on_collision_and_completes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that a collision on registration retries and then completes pairing."""
        original_settings = (
            settings.relay_backend_url,
            settings.relay_camera_id,
            settings.relay_api_key,
        )
        monkeypatch.setattr(pairing_mod, "_save_relay_credentials", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(pairing_mod.asyncio, "sleep", AsyncMock())
        client = FakeClient(
            post_responses=[FakeResponse(409), FakeResponse(201)],
            get_responses=[
                FakeResponse(200, {"status": pairing_mod.STATUS_WAITING}),
                FakeResponse(
                    200,
                    {
                        "status": pairing_mod.STATUS_PAIRED,
                        "camera_id": RELAY_CAMERA_ID,
                        "ws_url": RELAY_BACKEND_URL,
                        "api_key": RELAY_API_KEY,
                    },
                ),
            ],
        )
        on_paired = AsyncMock()
        generated = [(PAIRING_CODE_1, "FP1"), (PAIRING_CODE_2, "FP2")]
        monkeypatch.setattr(pairing_mod, "_generate_code_and_fingerprint", lambda: generated.pop(0))

        try:
            await pairing_mod._pairing_cycle(cast("Any", client), EXAMPLE_BACKEND_URL, on_paired)  # noqa: SLF001

            on_paired.assert_awaited_once()
            assert settings.relay_backend_url == RELAY_BACKEND_URL
            assert settings.relay_camera_id == RELAY_CAMERA_ID
            assert settings.relay_api_key == RELAY_API_KEY
        finally:
            settings.relay_backend_url, settings.relay_camera_id, settings.relay_api_key = original_settings

    async def test_saves_and_loads_credentials(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that credentials are saved to and loaded from disk correctly."""
        creds_file = tmp_path / "relay_credentials.json"
        monkeypatch.setattr(pairing_mod, "_CREDENTIALS_FILE", creds_file)

        pairing_mod._save_relay_credentials(RELAY_BACKEND_URL, RELAY_CAMERA_ID, RELAY_API_KEY)  # noqa: SLF001
        assert json.loads(creds_file.read_text()) == {
            "relay_backend_url": RELAY_BACKEND_URL,
            "relay_camera_id": RELAY_CAMERA_ID,
            "relay_api_key": RELAY_API_KEY,
        }
        assert pairing_mod.load_relay_credentials() == {
            "relay_backend_url": RELAY_BACKEND_URL,
            "relay_camera_id": RELAY_CAMERA_ID,
            "relay_api_key": RELAY_API_KEY,
        }

    async def test_load_relay_credentials_returns_none_when_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that missing credentials files return None."""
        monkeypatch.setattr(pairing_mod, "_CREDENTIALS_FILE", tmp_path / "missing.json")
        assert pairing_mod.load_relay_credentials() is None
