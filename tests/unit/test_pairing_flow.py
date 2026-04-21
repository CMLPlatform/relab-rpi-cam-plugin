"""Tests for the pairing flow helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.dependencies.auth import reload_authorized_hashes
from app.api.services import pairing as pairing_mod
from app.core.config import settings
from app.core.runtime import AppRuntime, set_active_runtime
from tests.constants import (
    EXAMPLE_BACKEND_URL,
    EXAMPLE_RELAY_BACKEND_URL,
    PAIRING_POLL_TIMEOUT_LOG,
    PAIRING_REGISTER_TIMEOUT_LOG,
    TRACEBACK_TEXT,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

RELAY_CAMERA_ID = "cam-1"
RELAY_AUTH_SCHEME = "device_assertion"
RELAY_KEY_ID = "key-1"
PAIRING_CODE_1 = "ABC123"
PAIRING_CODE_2 = "XYZ789"
PAIRING_MODE_LOG_PREFIX = "PAIRING MODE | state=awaiting_claim setup=/setup"
PAIRING_FAILURE_LOG = "Pairing cycle failed"
PAIRING_API_NOT_FOUND_LOG = "Pairing backend missing pairing API | stopping pairing"
FINGERPRINT_2 = "fingerprint-2"
LAN_SETUP_URL = "http://192.168.1.42:8018/setup"
RELATIVE_SETUP_PATH = "/setup"
PAIRING_STATE_ERROR = "error"
PAIRING_RETRY_ERROR = "retry"
PAIRING_HOST_ALIAS = "host.docker.internal"
PAIRING_REGISTER_REFUSAL = "refusing anonymous camera registration"
PAIRING_HTTP_500 = "HTTP 500"
PAIRING_STATUS_REGISTERING = "registering"
PAIRING_STATUS_IDLE = "idle"
FINGERPRINT_1 = "fingerprint-1"
PAIRING_UNREACHABLE_ERROR = "Pairing backend unreachable — retrying…"
PAIRING_RETRYING_ERROR = "Pairing failed — retrying…"
PAIRING_READ_WARNING = "Failed to read"
PAIRING_DELETE_WARNING = "Failed to delete relay credentials file"


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

    # Instance-level callables -- allow replacement with `AsyncMock` in tests.
    post: Any
    get: Any

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


@pytest.fixture(autouse=True)
def _active_runtime() -> Iterator[AppRuntime]:
    """Provide a runtime for helper paths that consult active runtime state."""
    runtime = AppRuntime()
    set_active_runtime(runtime)
    try:
        yield runtime
    finally:
        set_active_runtime(None)


class TestPairingHelpers:
    """Coverage for small helper branches."""

    def test_clear_transient_pairing_state_resets_public_state(self) -> None:
        """The transient state helper should clear the observable pairing fields."""
        state = pairing_mod.PairingState(code="CODE", fingerprint="FP", error="oops")
        cast("Any", state).expires_at = object()

        pairing_mod._clear_transient_pairing_state(state, status="error", error="retry")

        assert state.code is None
        assert state.fingerprint is None
        assert state.expires_at is None
        assert state.status == PAIRING_STATE_ERROR
        assert state.error == PAIRING_RETRY_ERROR

    def test_get_credentials_file_prefers_environment_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """The credentials file path should honor the explicit override env var."""
        override = tmp_path / "relay.json"
        monkeypatch.setenv("RELAB_CREDENTIALS_FILE", str(override))

        assert pairing_mod._get_credentials_file() == override

    def test_get_credentials_file_falls_back_to_xdg_style_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without an override, pairing should use the default config-home location."""
        monkeypatch.delenv("RELAB_CREDENTIALS_FILE", raising=False)
        assert str(pairing_mod._get_credentials_file()).endswith(".config/relab/relay_credentials.json")

    def test_pairing_service_reset_and_get_state(self) -> None:
        """Pairing service should expose and reset its observable state."""
        service = pairing_mod.PairingService()
        service.state.code = "CODE"
        service.state.status = "waiting"

        assert service.get_state() is service.state

        service.reset_state()

        assert service.state.code is None
        assert service.state.status == PAIRING_STATUS_IDLE

    def test_prepare_registration_state_logs_pairing_code(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The pairing code should be logged as soon as a registration is prepared."""
        registration = pairing_mod.PairingRegistration(
            code=PAIRING_CODE_1,
            fingerprint=FINGERPRINT_1,
            private_key=cast("Any", None),
            key_id=RELAY_KEY_ID,
            public_key_jwk={},
        )
        service = pairing_mod.PairingService()
        logged: list[str] = []

        def _log_ready(code: str) -> None:
            logged.append(code)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(pairing_mod, "_log_pairing_ready", _log_ready)
        with caplog.at_level(logging.INFO):
            service._prepare_registration_state(registration)
        monkeypatch.undo()

        assert logged == [PAIRING_CODE_1]
        assert service.state.status == PAIRING_STATUS_REGISTERING
        assert f"PAIRING CODE: {PAIRING_CODE_1}" in pairing_mod._format_pairing_ready_message(PAIRING_CODE_1)

    def test_log_pairing_connect_error_for_loopback_container_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Loopback backends inside containers should emit the host alias guidance."""
        monkeypatch.setattr(pairing_mod, "_is_running_in_container", lambda: True)
        with caplog.at_level(logging.ERROR):
            pairing_mod._log_pairing_connect_error(httpx.ConnectError("refused"), "http://localhost:8011")
        assert PAIRING_HOST_ALIAS in caplog.text

    def test_log_pairing_connect_error_for_normal_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Non-loopback connect errors should log the simpler unreachable message."""
        monkeypatch.setattr(pairing_mod, "_is_running_in_container", lambda: False)
        with caplog.at_level(logging.ERROR):
            pairing_mod._log_pairing_connect_error(httpx.ConnectError("refused"), EXAMPLE_BACKEND_URL)
        assert f"Pairing backend {EXAMPLE_BACKEND_URL} could not be reached." in caplog.text

    def test_log_pairing_http_status_error_variants(self, caplog: pytest.LogCaptureFixture) -> None:
        """Register-specific 403s and generic failures should both be logged."""
        request = httpx.Request("POST", f"{EXAMPLE_BACKEND_URL}/plugins/rpi-cam/pairing/register")
        forbidden = httpx.HTTPStatusError(
            "forbidden",
            request=request,
            response=httpx.Response(403, request=request, text="nope"),
        )
        generic = httpx.HTTPStatusError(
            "bad",
            request=request,
            response=httpx.Response(500, request=request, text="boom"),
        )

        with caplog.at_level(logging.ERROR):
            pairing_mod._log_pairing_http_status_error(forbidden)
            pairing_mod._log_pairing_http_status_error(generic)

        assert PAIRING_REGISTER_REFUSAL in caplog.text
        assert PAIRING_HTTP_500 in caplog.text

    def test_log_pairing_http_status_error_truncates_long_response_body(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Very long backend bodies should be trimmed in logs."""
        request = httpx.Request("GET", f"{EXAMPLE_BACKEND_URL}/plugins/rpi-cam/pairing/poll")
        long_body = "x" * 300
        error = httpx.HTTPStatusError(
            "bad",
            request=request,
            response=httpx.Response(500, request=request, text=long_body),
        )

        with caplog.at_level(logging.ERROR):
            pairing_mod._log_pairing_http_status_error(error)

        assert f"{'x' * 157}..." in caplog.text

    def test_prepare_registration_state_helper_sets_status_and_logs_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The module-level helper should mirror the service helper behavior."""
        state = pairing_mod.PairingState()
        logged: list[str] = []
        registration = pairing_mod.PairingRegistration(
            code=PAIRING_CODE_1,
            fingerprint=FINGERPRINT_1,
            private_key=cast("Any", None),
            key_id=RELAY_KEY_ID,
            public_key_jwk={},
        )
        monkeypatch.setattr(pairing_mod, "_log_pairing_ready", logged.append)

        pairing_mod._prepare_registration_state(state, registration)

        assert state.code == PAIRING_CODE_1
        assert state.status == PAIRING_STATUS_REGISTERING
        assert logged == [PAIRING_CODE_1]

    def test_new_pairing_registration_generates_all_material(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A new registration should include generated code, key id, and JWK."""
        private_key = pairing_mod._generate_private_key()
        monkeypatch.setattr(pairing_mod, "_generate_code_and_fingerprint", lambda: (PAIRING_CODE_1, FINGERPRINT_1))
        monkeypatch.setattr(pairing_mod, "_generate_private_key", lambda: private_key)
        monkeypatch.setattr(pairing_mod.secrets, "token_urlsafe", lambda _n: RELAY_KEY_ID)

        registration = pairing_mod._new_pairing_registration()

        assert registration.code == PAIRING_CODE_1
        assert registration.fingerprint == FINGERPRINT_1
        assert registration.key_id == RELAY_KEY_ID
        assert registration.public_key_jwk["kid"] == RELAY_KEY_ID


class TestRunPairing:
    """Tests for the top-level pairing loop."""

    async def test_noop_when_backend_url_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that pairing exits immediately when the backend URL is unset."""
        monkeypatch.setattr(settings, "pairing_backend_url", "")
        on_paired = AsyncMock()
        await pairing_mod.PairingService().run_forever(on_paired)
        on_paired.assert_not_awaited()

    async def test_stops_when_register_endpoint_is_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A 404 on the register endpoint should stop pairing instead of retrying forever."""
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        monkeypatch.setattr(
            pairing_mod.httpx,
            "AsyncClient",
            lambda *_args, **_kwargs: FakeClient([FakeResponse(404)], []),
        )
        on_paired = AsyncMock()
        service = pairing_mod.PairingService()

        with caplog.at_level(logging.ERROR):
            await service.run_forever(on_paired)

        on_paired.assert_not_awaited()
        assert PAIRING_API_NOT_FOUND_LOG in caplog.text
        assert TRACEBACK_TEXT in caplog.text

    async def test_rewrites_loopback_backend_to_host_docker_internal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Loopback pairing backends should target the Docker host when containerized."""
        monkeypatch.setattr(settings, "pairing_backend_url", "http://localhost:8011")
        monkeypatch.setattr(pairing_mod, "_is_running_in_container", lambda: True)
        seen: list[str] = []
        service = pairing_mod.PairingService()

        async def fake_pairing_cycle(
            _client: object,
            base_url: str,
            _on_paired: object,
        ) -> None:
            seen.append(base_url)

        monkeypatch.setattr(service, "_pairing_cycle", fake_pairing_cycle)
        monkeypatch.setattr(pairing_mod.httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient([], []))

        await service.run_forever(AsyncMock())

        assert seen == ["http://host.docker.internal:8011"]

    async def test_retries_after_http_status_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP status failures should clear state, sleep, and retry the loop."""
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        monkeypatch.setattr(pairing_mod.httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient([], []))
        sleep_calls: list[int] = []

        async def _record_sleep(delay: int) -> None:
            sleep_calls.append(delay)

        monkeypatch.setattr(pairing_mod.asyncio, "sleep", AsyncMock(side_effect=_record_sleep))
        service = pairing_mod.PairingService()
        request = httpx.Request("POST", f"{EXAMPLE_BACKEND_URL}/plugins/rpi-cam/pairing/register")
        error = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(500, request=request))
        attempts = {"count": 0}

        async def fake_pairing_cycle(_client: object, _base_url: str, _on_paired: object) -> None:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise error

        monkeypatch.setattr(service, "_pairing_cycle", fake_pairing_cycle)

        await service.run_forever(AsyncMock())

        assert attempts["count"] == 2
        assert service.state.status == PAIRING_STATE_ERROR
        assert sleep_calls == [10]

    async def test_retries_after_connect_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Connect errors should follow the same retry path with the unreachable message."""
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        monkeypatch.setattr(pairing_mod.httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient([], []))
        monkeypatch.setattr(pairing_mod.asyncio, "sleep", AsyncMock())
        service = pairing_mod.PairingService()
        attempts = {"count": 0}

        async def fake_pairing_cycle(_client: object, _base_url: str, _on_paired: object) -> None:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise httpx.ConnectError(PAIRING_REGISTER_REFUSAL)

        monkeypatch.setattr(service, "_pairing_cycle", fake_pairing_cycle)

        await service.run_forever(AsyncMock())

        assert attempts["count"] == 2
        assert service.state.error == PAIRING_UNREACHABLE_ERROR

    async def test_retries_after_unexpected_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unexpected errors should also clear transient state and retry."""
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        monkeypatch.setattr(pairing_mod.httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient([], []))
        monkeypatch.setattr(pairing_mod.asyncio, "sleep", AsyncMock())
        service = pairing_mod.PairingService()
        attempts = {"count": 0}

        async def fake_pairing_cycle(_client: object, _base_url: str, _on_paired: object) -> None:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError(PAIRING_FAILURE_LOG)

        monkeypatch.setattr(service, "_pairing_cycle", fake_pairing_cycle)

        await service.run_forever(AsyncMock())

        assert attempts["count"] == 2
        assert service.state.error == PAIRING_RETRYING_ERROR


class TestPairingCycle:
    """Tests for a single pairing cycle."""

    async def test_retries_on_collision_and_completes(
        self,
        app_runtime: AppRuntime,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test that a collision on registration retries and then completes pairing."""
        original_snapshot = app_runtime.runtime_state.authorized_api_keys
        set_active_runtime(app_runtime)
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        monkeypatch.setattr(settings, "base_url", "http://127.0.0.1:8018/")
        monkeypatch.setattr(pairing_mod, "_lan_setup_url", lambda _port: None)
        monkeypatch.setattr(pairing_mod, "_save_relay_credentials", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(pairing_mod.asyncio, "sleep", AsyncMock())
        client: Any = FakeClient(
            post_responses=[FakeResponse(409), FakeResponse(201)],
            get_responses=[
                FakeResponse(200, {"status": pairing_mod.STATUS_WAITING}),
                FakeResponse(
                    200,
                    {
                        "status": pairing_mod.STATUS_PAIRED,
                        "camera_id": RELAY_CAMERA_ID,
                        "ws_url": EXAMPLE_RELAY_BACKEND_URL,
                        "auth_scheme": RELAY_AUTH_SCHEME,
                        "key_id": RELAY_KEY_ID,
                    },
                ),
            ],
        )
        on_paired = AsyncMock()
        generated = [(PAIRING_CODE_1, FINGERPRINT_1), (PAIRING_CODE_2, FINGERPRINT_2)]
        monkeypatch.setattr(pairing_mod, "_generate_code_and_fingerprint", lambda: generated.pop(0))
        service = pairing_mod.PairingService()

        try:
            with caplog.at_level(logging.INFO):
                service.log_mode_started()
            await service._pairing_cycle(cast("Any", client), EXAMPLE_BACKEND_URL, on_paired)

            on_paired.assert_awaited_once()
            assert app_runtime.runtime_state.relay_backend_url == EXAMPLE_RELAY_BACKEND_URL
            assert app_runtime.runtime_state.relay_camera_id == RELAY_CAMERA_ID
            assert app_runtime.runtime_state.relay_auth_scheme == RELAY_AUTH_SCHEME
            assert app_runtime.runtime_state.relay_key_id == RELAY_KEY_ID
            assert app_runtime.runtime_state.relay_private_key_pem
            assert app_runtime.runtime_state.local_relay_api_key.startswith("LOCAL_")
            assert app_runtime.runtime_state.local_relay_api_key in app_runtime.runtime_state.authorized_api_keys
            log_text = caplog.text
            assert PAIRING_MODE_LOG_PREFIX in log_text
            assert pairing_mod._format_pairing_ready_message(PAIRING_CODE_2) in log_text
            assert f"PAIRING COMPLETE | camera_id={RELAY_CAMERA_ID} relay_starting=true" in log_text
            assert app_runtime.runtime_state.relay_private_key_pem not in log_text
            assert FINGERPRINT_2 not in log_text
        finally:
            set_active_runtime(None)
            app_runtime.runtime_state.clear_relay_credentials()
            app_runtime.runtime_state.local_relay_api_key = ""
            app_runtime.runtime_state.replace_authorized_api_keys(original_snapshot)
            reload_authorized_hashes(app_runtime.runtime_state)

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
        service = pairing_mod.PairingService()
        client: Any = FakeClient(
            post_responses=[FakeResponse(201), FakeResponse(201)],
            get_responses=[
                FakeResponse(404),
                FakeResponse(
                    200,
                    {
                        "status": pairing_mod.STATUS_PAIRED,
                        "camera_id": RELAY_CAMERA_ID,
                        "ws_url": EXAMPLE_RELAY_BACKEND_URL,
                        "auth_scheme": RELAY_AUTH_SCHEME,
                        "key_id": RELAY_KEY_ID,
                    },
                ),
            ],
        )
        on_paired = AsyncMock()
        generated = [(PAIRING_CODE_1, FINGERPRINT_1), (PAIRING_CODE_2, FINGERPRINT_2)]
        monkeypatch.setattr(pairing_mod, "_generate_code_and_fingerprint", lambda: generated.pop(0))
        monkeypatch.setattr(pairing_mod.httpx, "AsyncClient", lambda *_args, **_kwargs: client)

        with caplog.at_level(logging.INFO):
            await service.run_forever(on_paired)

        on_paired.assert_awaited_once()
        log_text = caplog.text
        assert pairing_mod._format_pairing_ready_message(PAIRING_CODE_1) in log_text
        assert f"PAIRING ROTATING | expired_code={PAIRING_CODE_1} reason=expired" in log_text
        assert pairing_mod._format_pairing_ready_message(PAIRING_CODE_2) in log_text
        assert PAIRING_FAILURE_LOG not in log_text

    async def test_register_timeout_retries_same_cycle_without_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A timeout while registering should be retried before the pairing cycle gives up."""
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        monkeypatch.setattr(settings, "base_url", "https://camera.example/")
        monkeypatch.setattr(pairing_mod, "_save_relay_credentials", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(pairing_mod.asyncio, "sleep", AsyncMock())
        service = pairing_mod.PairingService()
        client: Any = FakeClient(
            post_responses=[FakeResponse(201)],
            get_responses=[
                FakeResponse(200, {"status": pairing_mod.STATUS_WAITING}),
                FakeResponse(
                    200,
                    {
                        "status": pairing_mod.STATUS_PAIRED,
                        "camera_id": RELAY_CAMERA_ID,
                        "ws_url": EXAMPLE_RELAY_BACKEND_URL,
                        "auth_scheme": RELAY_AUTH_SCHEME,
                        "key_id": RELAY_KEY_ID,
                    },
                ),
            ],
        )
        timeout_request = httpx.Request("POST", f"{EXAMPLE_BACKEND_URL}/plugins/rpi-cam/pairing/register")
        cast("Any", client).post = AsyncMock(
            side_effect=[httpx.ReadTimeout("register timed out", request=timeout_request), FakeResponse(201)]
        )
        monkeypatch.setattr(pairing_mod.httpx, "AsyncClient", lambda *_args, **_kwargs: client)
        monkeypatch.setattr(pairing_mod, "_generate_code_and_fingerprint", lambda: (PAIRING_CODE_1, FINGERPRINT_1))
        on_paired = AsyncMock()

        with caplog.at_level(logging.WARNING):
            await service.run_forever(on_paired)

        on_paired.assert_awaited_once()
        assert PAIRING_REGISTER_TIMEOUT_LOG in caplog.text
        assert TRACEBACK_TEXT not in caplog.text

    async def test_poll_timeout_retries_same_cycle_without_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A timeout while polling should be treated as transient and retried in place."""
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        monkeypatch.setattr(settings, "base_url", "https://camera.example/")
        monkeypatch.setattr(pairing_mod, "_save_relay_credentials", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(pairing_mod.asyncio, "sleep", AsyncMock())
        service = pairing_mod.PairingService()
        client: Any = FakeClient(
            post_responses=[FakeResponse(201)],
            get_responses=[
                FakeResponse(200, {"status": pairing_mod.STATUS_WAITING}),
                FakeResponse(
                    200,
                    {
                        "status": pairing_mod.STATUS_PAIRED,
                        "camera_id": RELAY_CAMERA_ID,
                        "ws_url": EXAMPLE_RELAY_BACKEND_URL,
                        "auth_scheme": RELAY_AUTH_SCHEME,
                        "key_id": RELAY_KEY_ID,
                    },
                ),
            ],
        )
        waiting_response = FakeResponse(200, {"status": pairing_mod.STATUS_WAITING})
        paired_response = FakeResponse(
            200,
            {
                "status": pairing_mod.STATUS_PAIRED,
                "camera_id": RELAY_CAMERA_ID,
                "ws_url": EXAMPLE_RELAY_BACKEND_URL,
                "auth_scheme": RELAY_AUTH_SCHEME,
                "key_id": RELAY_KEY_ID,
            },
        )
        timeout_request = httpx.Request("GET", f"{EXAMPLE_BACKEND_URL}/plugins/rpi-cam/pairing/poll")
        cast("Any", client).get = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("poll timed out", request=timeout_request),
                waiting_response,
                paired_response,
            ]
        )
        monkeypatch.setattr(pairing_mod.httpx, "AsyncClient", lambda *_args, **_kwargs: client)
        monkeypatch.setattr(pairing_mod, "_generate_code_and_fingerprint", lambda: (PAIRING_CODE_1, FINGERPRINT_1))
        on_paired = AsyncMock()

        with caplog.at_level(logging.WARNING):
            await service.run_forever(on_paired)

        on_paired.assert_awaited_once()
        assert PAIRING_POLL_TIMEOUT_LOG in caplog.text
        assert TRACEBACK_TEXT not in caplog.text

    def test_pairing_mode_prefers_detected_lan_setup_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loopback base URLs should prefer a best-effort LAN setup URL in logs."""
        monkeypatch.setattr(settings, "base_url", "http://127.0.0.1:8018/")
        monkeypatch.setattr(pairing_mod.socket, "gethostname", lambda: "rpi-cam")
        monkeypatch.setattr(
            pairing_mod.socket,
            "gethostbyname_ex",
            lambda _host: ("rpi-cam", [], ["127.0.0.1", "192.168.1.42"]),
        )

        assert pairing_mod._pairing_setup_location() == LAN_SETUP_URL

    def test_pairing_mode_falls_back_to_relative_setup_path_without_lan_address(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If no non-loopback LAN address is available, logs should keep the safe /setup fallback."""
        monkeypatch.setattr(settings, "base_url", "http://127.0.0.1:8018/")
        monkeypatch.setattr(pairing_mod.socket, "gethostname", lambda: "rpi-cam")
        monkeypatch.setattr(pairing_mod.socket, "gethostbyname_ex", lambda _host: ("rpi-cam", [], ["127.0.0.1"]))

        assert pairing_mod._pairing_setup_location() == RELATIVE_SETUP_PATH

    def test_normalize_pairing_backend_base_url_keeps_non_loopback_host(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Real backend hosts should be left untouched."""
        monkeypatch.setattr(pairing_mod, "_is_running_in_container", lambda: True)
        assert pairing_mod._normalize_pairing_backend_base_url(EXAMPLE_BACKEND_URL) == EXAMPLE_BACKEND_URL

    async def test_saves_and_loads_credentials(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that credentials are saved to and loaded from disk correctly."""
        creds_file = tmp_path / "relay_credentials.json"
        monkeypatch.setattr(pairing_mod, "_CREDENTIALS_FILE", creds_file)
        private_key = pairing_mod._private_key_pem(pairing_mod._generate_private_key())

        pairing_mod._save_relay_credentials(
            EXAMPLE_RELAY_BACKEND_URL,
            RELAY_CAMERA_ID,
            RELAY_AUTH_SCHEME,
            RELAY_KEY_ID,
            private_key,
        )
        assert json.loads(creds_file.read_text()) == {
            "relay_backend_url": EXAMPLE_RELAY_BACKEND_URL,
            "relay_camera_id": RELAY_CAMERA_ID,
            "relay_auth_scheme": RELAY_AUTH_SCHEME,
            "relay_key_id": RELAY_KEY_ID,
            "relay_private_key_pem": private_key,
        }
        assert pairing_mod.load_relay_credentials() == {
            "relay_backend_url": EXAMPLE_RELAY_BACKEND_URL,
            "relay_camera_id": RELAY_CAMERA_ID,
            "relay_auth_scheme": RELAY_AUTH_SCHEME,
            "relay_key_id": RELAY_KEY_ID,
            "relay_private_key_pem": private_key,
        }

    async def test_load_relay_credentials_returns_none_when_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that missing credentials files return None."""
        monkeypatch.setattr(pairing_mod, "_CREDENTIALS_FILE", tmp_path / "missing.json")
        assert pairing_mod.load_relay_credentials() is None

    async def test_register_pairing_code_raises_after_three_timeouts(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Three register timeouts in one cycle should surface a runtime error."""
        state = pairing_mod.PairingState()
        request = httpx.Request("POST", f"{EXAMPLE_BACKEND_URL}/plugins/rpi-cam/pairing/register")
        client = cast("Any", FakeClient([], []))
        client.post = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("timeout-1", request=request),
                httpx.ReadTimeout("timeout-2", request=request),
                httpx.ReadTimeout("timeout-3", request=request),
            ]
        )
        monkeypatch.setattr(pairing_mod.asyncio, "sleep", AsyncMock())
        monkeypatch.setattr(pairing_mod, "_generate_code_and_fingerprint", lambda: (PAIRING_CODE_1, FINGERPRINT_1))

        with pytest.raises(RuntimeError, match="Failed to register pairing code after 3 attempts"):
            await pairing_mod._register_pairing_code(client, EXAMPLE_BACKEND_URL, state)

    async def test_complete_pairing_updates_state_and_runtime(
        self,
        app_runtime: AppRuntime,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The module-level complete helper should persist creds and update runtime state."""
        state = pairing_mod.PairingState(
            code=PAIRING_CODE_1,
            fingerprint=FINGERPRINT_1,
            status=pairing_mod.STATUS_WAITING,
        )
        private_key = pairing_mod._generate_private_key()
        saved: list[tuple[str, str, str, str, str]] = []
        on_paired = AsyncMock()
        monkeypatch.setattr(
            pairing_mod,
            "_save_relay_credentials",
            lambda **kwargs: saved.append(
                (
                    str(kwargs["relay_backend_url"]),
                    str(kwargs["camera_id"]),
                    str(kwargs["relay_auth_scheme"]),
                    str(kwargs["key_id"]),
                    str(kwargs["private_key_pem"]),
                )
            ),
        )
        set_active_runtime(app_runtime)

        await pairing_mod._complete_pairing(
            state,
            {
                "camera_id": RELAY_CAMERA_ID,
                "ws_url": EXAMPLE_RELAY_BACKEND_URL,
                "auth_scheme": RELAY_AUTH_SCHEME,
                "key_id": RELAY_KEY_ID,
            },
            private_key,
            on_paired,
        )

        assert state.status == pairing_mod.STATUS_PAIRED
        assert state.code is None
        assert app_runtime.runtime_state.relay_backend_url == EXAMPLE_RELAY_BACKEND_URL
        assert saved
        assert saved[0][:4] == (
            EXAMPLE_RELAY_BACKEND_URL,
            RELAY_CAMERA_ID,
            RELAY_AUTH_SCHEME,
            RELAY_KEY_ID,
        )
        on_paired.assert_awaited_once()

    async def test_save_relay_credentials_cleans_up_temp_file_on_replace_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed replace should remove the temp file and re-raise."""
        creds_file = tmp_path / "relay_credentials.json"
        monkeypatch.setattr(pairing_mod, "_CREDENTIALS_FILE", creds_file)
        observed_tmp_paths: list[Path] = []
        original_replace = Path.replace

        def _failing_replace(path: Path, target: Path) -> Path:
            del target
            observed_tmp_paths.append(path)
            raise OSError(PAIRING_FAILURE_LOG)

        monkeypatch.setattr(Path, "replace", _failing_replace)

        with pytest.raises(OSError, match=PAIRING_FAILURE_LOG):
            pairing_mod._save_relay_credentials(
                EXAMPLE_RELAY_BACKEND_URL,
                RELAY_CAMERA_ID,
                RELAY_AUTH_SCHEME,
                RELAY_KEY_ID,
                "pem",
            )

        assert observed_tmp_paths
        assert not observed_tmp_paths[0].exists()
        monkeypatch.setattr(Path, "replace", original_replace)

    async def test_load_relay_credentials_returns_none_for_invalid_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unreadable credentials files should warn and return None."""
        creds_file = tmp_path / "relay_credentials.json"
        creds_file.write_text("{invalid")
        monkeypatch.setattr(pairing_mod, "_CREDENTIALS_FILE", creds_file)

        with caplog.at_level(logging.WARNING):
            assert pairing_mod.load_relay_credentials() is None

        assert PAIRING_READ_WARNING in caplog.text

    async def test_delete_relay_credentials_logs_warning_on_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Delete failures should be logged without crashing the caller."""
        creds_file = tmp_path / "relay_credentials.json"
        creds_file.write_text("x")
        monkeypatch.setattr(pairing_mod, "_CREDENTIALS_FILE", creds_file)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError(PAIRING_FAILURE_LOG)

        monkeypatch.setattr(Path, "unlink", _boom)

        with caplog.at_level(logging.WARNING):
            pairing_mod.delete_relay_credentials()

        assert PAIRING_DELETE_WARNING in caplog.text
