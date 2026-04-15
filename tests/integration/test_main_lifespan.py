"""Tests for application lifespan startup and shutdown."""

import asyncio
import logging
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

import app.main as main_mod
import app.utils.pairing as pairing_mod
from app.core.config import settings
from app.core.runtime import AppRuntime
from tests.constants import EXAMPLE_BACKEND_URL, EXAMPLE_RELAY_BACKEND_URL
from tests.support.fakes import (
    FakePairingService,
    FakePreviewSleeper,
    FakeRelayService,
    FakeThermalGovernor,
    StubCameraManager,
)

PAIRING_MODE_LOG = f"PAIRING MODE | state=awaiting_claim setup=/setup pairing_backend={EXAMPLE_BACKEND_URL}"
STARTUP_BANNER_SETUP_URL = "Setup    : http://<this-ip>:8018/setup"
STARTUP_BANNER_PAIRING_NOTE = "Note     : pairing code will appear below in a boxed log banner"
LOCAL_DNS_SUFFIX = ".local"


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> AppRuntime:
    """Provide a stub runtime so lifespan tests don't depend on real globals."""

    class LoggingPairingService(FakePairingService):
        def log_mode_started(self) -> None:
            super().log_mode_started()
            logging.getLogger("app.utils.pairing").info(PAIRING_MODE_LOG)

    runtime = AppRuntime()
    runtime.camera_manager = StubCameraManager()
    runtime.preview_sleeper = FakePreviewSleeper(runtime)
    runtime.thermal_governor = FakeThermalGovernor(runtime)
    runtime.relay_service = FakeRelayService(runtime)
    runtime.pairing_service = LoggingPairingService()
    runtime.observability_handle = None
    monkeypatch.setattr(main_mod, "ensure_app_runtime", lambda _app: runtime)
    return runtime


async def _run_lifespan_once(app: FastAPI) -> None:
    """Helper to run the lifespan of the app once."""
    async with main_mod.lifespan(app):
        await asyncio.sleep(0)


class TestLifespan:
    """Tests for the FastAPI lifespan hook."""

    @pytest.mark.usefixtures("runtime")
    async def test_startup_banner_uses_placeholder_setup_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The startup banner should not advertise a container-looking mDNS hostname."""
        app = FastAPI()
        monkeypatch.setattr(settings, "relay_backend_url", "")
        monkeypatch.setattr(settings, "relay_camera_id", "")
        monkeypatch.setattr(settings, "relay_key_id", "")
        monkeypatch.setattr(settings, "relay_private_key_pem", "")
        monkeypatch.setattr(settings, "pairing_backend_url", "")
        monkeypatch.setattr(settings, "base_url", "http://127.0.0.1:8018/")
        monkeypatch.setattr(main_mod, "bootstrap_runtime_state", lambda _runtime_state: None)
        monkeypatch.setattr(main_mod, "setup_directory", AsyncMock())

        with caplog.at_level(logging.INFO):
            await _run_lifespan_once(app)

        assert STARTUP_BANNER_SETUP_URL in caplog.text
        assert STARTUP_BANNER_PAIRING_NOTE not in caplog.text
        assert LOCAL_DNS_SUFFIX not in caplog.text

    async def test_relay_enabled_starts_relay_and_cleans_up(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runtime: AppRuntime,
    ) -> None:
        """Test that when relay credentials are set, the relay is started on startup and cleaned up on shutdown."""
        app = FastAPI()
        monkeypatch.setattr(settings, "relay_backend_url", EXAMPLE_RELAY_BACKEND_URL)
        monkeypatch.setattr(settings, "relay_camera_id", "cam-1")
        monkeypatch.setattr(settings, "relay_auth_scheme", "device_assertion")
        monkeypatch.setattr(settings, "relay_key_id", "key-1")
        monkeypatch.setattr(settings, "relay_private_key_pem", "private-key")
        monkeypatch.setattr(settings, "pairing_backend_url", "")
        runtime.runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme="device_assertion",
            relay_key_id="key-1",
            relay_private_key_pem="private-key",
        )
        monkeypatch.setattr(main_mod, "bootstrap_runtime_state", lambda _runtime_state: None)
        setup_calls: list[object] = []

        async def _setup_directory(path: object) -> object:
            setup_calls.append(path)
            return path

        monkeypatch.setattr(main_mod, "setup_directory", _setup_directory)

        await _run_lifespan_once(app)

        assert setup_calls == [settings.image_path]
        assert {task.get_name() for task in runtime.background_tasks | runtime.recurring_tasks} == set()
        camera_manager = cast("StubCameraManager", runtime.camera_manager)
        assert camera_manager.cleanup_calls == [True]

    async def test_pairing_mode_starts_relay_after_pairing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        runtime: AppRuntime,
    ) -> None:
        """Test that when no relay credentials are set, the relay is started after pairing."""
        app = FastAPI()
        monkeypatch.setattr(settings, "relay_backend_url", "")
        monkeypatch.setattr(settings, "relay_camera_id", "")
        monkeypatch.setattr(settings, "relay_key_id", "")
        monkeypatch.setattr(settings, "relay_private_key_pem", "")
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        monkeypatch.setattr(settings, "base_url", "http://127.0.0.1:8018/")
        monkeypatch.setattr(pairing_mod, "_lan_setup_url", lambda _port: None)
        monkeypatch.setattr(main_mod, "bootstrap_runtime_state", lambda _runtime_state: None)
        monkeypatch.setattr(main_mod, "setup_directory", AsyncMock())

        with caplog.at_level(logging.INFO):
            await _run_lifespan_once(app)

        assert PAIRING_MODE_LOG in caplog.text
        assert STARTUP_BANNER_PAIRING_NOTE in caplog.text
        pairing_service = cast("FakePairingService", runtime.pairing_service)
        camera_manager = cast("StubCameraManager", runtime.camera_manager)
        assert pairing_service.log_calls == 1
        assert camera_manager.cleanup_calls == [True]
