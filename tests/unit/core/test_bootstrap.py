"""Unit tests for startup bootstrap warnings."""

import logging

import pytest
from pydantic import HttpUrl

from app.core.bootstrap import bootstrap_runtime_state
from app.core.runtime_state import RuntimeState
from app.core.settings import APP_ENV_DEVELOPMENT, APP_ENV_PRODUCTION, Settings

_PLAINTEXT_WARNING = "BASE_URL uses plain HTTP"


def _settings(**overrides: object) -> Settings:
    return Settings(pairing_backend_url="", **overrides)  # type: ignore[arg-type]


class TestPlaintextBaseUrlWarning:
    def test_warns_on_http_base_url_in_production(self, caplog: pytest.LogCaptureFixture) -> None:
        app_settings = _settings(base_url=HttpUrl("http://camera.example:8018"), app_env=APP_ENV_PRODUCTION)
        with caplog.at_level(logging.WARNING):
            bootstrap_runtime_state(RuntimeState(), app_settings)
        assert _PLAINTEXT_WARNING in caplog.text

    def test_silent_on_https_base_url(self, caplog: pytest.LogCaptureFixture) -> None:
        app_settings = _settings(base_url=HttpUrl("https://camera.example"), app_env=APP_ENV_PRODUCTION)
        with caplog.at_level(logging.WARNING):
            bootstrap_runtime_state(RuntimeState(), app_settings)
        assert _PLAINTEXT_WARNING not in caplog.text

    def test_silent_on_http_in_development(self, caplog: pytest.LogCaptureFixture) -> None:
        app_settings = _settings(base_url=HttpUrl("http://camera.example:8018"), app_env=APP_ENV_DEVELOPMENT)
        with caplog.at_level(logging.WARNING):
            bootstrap_runtime_state(RuntimeState(), app_settings)
        assert _PLAINTEXT_WARNING not in caplog.text
