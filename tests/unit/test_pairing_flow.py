"""Tests for the pairing flow helpers."""

import json
import logging
from pathlib import Path
from typing import Any, Self, cast
from unittest.mock import AsyncMock

import pytest

from app.api.dependencies.auth import reload_authorized_hashes
from app.core.config import settings
from app.utils import pairing as pairing_mod

EXAMPLE_BACKEND_URL = "https://example.com"
RELAY_BACKEND_URL = "wss://example.com/ws"
RELAY_CAMERA_ID = "cam-1"
RELAY_API_KEY = "key-1"
PAIRING_CODE_1 = "CODE1"
PAIRING_CODE_2 = "CODE2"
PAIRING_MODE_LOG_PREFIX = "PAIRING MODE | state=awaiting_claim setup=/setup"
PAIRING_FAILURE_LOG = "Pairing cycle failed"
FINGERPRINT_2 = "FP2"
LAN_SETUP_URL = "http://192.168.1.42:8018/setup"
RELATIVE_SETUP_PATH = "/setup"


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

    async def __aenter__(self) -> Self:
        """Support async context manager usage like httpx.AsyncClient."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """No-op async context manager exit."""
        return

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

    async def test_rewrites_loopback_backend_to_host_docker_internal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Loopback pairing backends should target the Docker host when containerized."""
        monkeypatch.setattr(settings, "pairing_backend_url", "http://localhost:8011")
        monkeypatch.setattr(pairing_mod, "_is_running_in_container", lambda: True)
        seen: list[str] = []

        async def fake_pairing_cycle(
            _client: Any,
            base_url: str,
            _on_paired: Any,
        ) -> None:
            seen.append(base_url)
            return None

        monkeypatch.setattr(pairing_mod, "_pairing_cycle", fake_pairing_cycle)
        monkeypatch.setattr(pairing_mod.httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient([], []))

        await pairing_mod.run_pairing(AsyncMock())

        assert seen == ["http://host.docker.internal:8011"]


class TestPairingCycle:
    """Tests for a single pairing cycle."""

    async def test_retries_on_collision_and_completes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test that a collision on registration retries and then completes pairing."""
        original_settings = (
            settings.relay_backend_url,
            settings.relay_camera_id,
            settings.relay_api_key,
            list(settings.authorized_api_keys),
        )
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        monkeypatch.setattr(settings, "base_url", "http://127.0.0.1:8018/")
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
            with caplog.at_level(logging.INFO):
                pairing_mod.log_pairing_mode_started()
            await pairing_mod._pairing_cycle(cast("Any", client), EXAMPLE_BACKEND_URL, on_paired)  # noqa: SLF001

            on_paired.assert_awaited_once()
            assert settings.relay_backend_url == RELAY_BACKEND_URL
            assert settings.relay_camera_id == RELAY_CAMERA_ID
            assert settings.relay_api_key == RELAY_API_KEY
            assert RELAY_API_KEY in settings.authorized_api_keys
            log_text = caplog.text
            assert PAIRING_MODE_LOG_PREFIX in log_text
            assert pairing_mod._format_pairing_ready_banner(PAIRING_CODE_2) in log_text  # noqa: SLF001
            assert f"PAIRING COMPLETE | camera_id={RELAY_CAMERA_ID} relay_starting=true" in log_text
            assert RELAY_API_KEY not in log_text
            assert FINGERPRINT_2 not in log_text
        finally:
            (
                settings.relay_backend_url,
                settings.relay_camera_id,
                settings.relay_api_key,
                settings.authorized_api_keys,
            ) = original_settings
            reload_authorized_hashes()

    async def test_expired_code_rotates_without_error_stacktrace(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Expired pairing codes should rotate cleanly and log the new ready code."""
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        monkeypatch.setattr(settings, "base_url", "https://camera.example/")
        monkeypatch.setattr(pairing_mod, "_save_relay_credentials", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(pairing_mod.asyncio, "sleep", AsyncMock())
        client = FakeClient(
            post_responses=[FakeResponse(201), FakeResponse(201)],
            get_responses=[
                FakeResponse(404),
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
        monkeypatch.setattr(pairing_mod.httpx, "AsyncClient", lambda *_args, **_kwargs: client)

        with caplog.at_level(logging.INFO):
            await pairing_mod.run_pairing(on_paired)

        on_paired.assert_awaited_once()
        log_text = caplog.text
        assert pairing_mod._format_pairing_ready_banner(PAIRING_CODE_1) in log_text  # noqa: SLF001
        assert f"PAIRING ROTATING | expired_code={PAIRING_CODE_1} reason=expired" in log_text
        assert pairing_mod._format_pairing_ready_banner(PAIRING_CODE_2) in log_text  # noqa: SLF001
        assert PAIRING_FAILURE_LOG not in log_text

    def test_pairing_mode_prefers_detected_lan_setup_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loopback base URLs should prefer a best-effort LAN setup URL in logs."""
        monkeypatch.setattr(settings, "base_url", "http://127.0.0.1:8018/")
        monkeypatch.setattr(pairing_mod.socket, "gethostname", lambda: "rpi-cam")
        monkeypatch.setattr(
            pairing_mod.socket,
            "gethostbyname_ex",
            lambda _host: ("rpi-cam", [], ["127.0.0.1", "192.168.1.42"]),
        )

        assert pairing_mod._pairing_setup_location() == LAN_SETUP_URL  # noqa: SLF001

    def test_pairing_mode_falls_back_to_relative_setup_path_without_lan_address(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If no non-loopback LAN address is available, logs should keep the safe /setup fallback."""
        monkeypatch.setattr(settings, "base_url", "http://127.0.0.1:8018/")
        monkeypatch.setattr(pairing_mod.socket, "gethostname", lambda: "rpi-cam")
        monkeypatch.setattr(pairing_mod.socket, "gethostbyname_ex", lambda _host: ("rpi-cam", [], ["127.0.0.1"]))

        assert pairing_mod._pairing_setup_location() == RELATIVE_SETUP_PATH  # noqa: SLF001

    def test_normalize_pairing_backend_base_url_keeps_non_loopback_host(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Real backend hosts should be left untouched."""
        monkeypatch.setattr(pairing_mod, "_is_running_in_container", lambda: True)
        assert pairing_mod._normalize_pairing_backend_base_url(EXAMPLE_BACKEND_URL) == EXAMPLE_BACKEND_URL  # noqa: SLF001

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
