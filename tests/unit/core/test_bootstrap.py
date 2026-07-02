"""Unit tests for startup bootstrap warnings."""

import logging
from typing import Any

import pytest
from pydantic import HttpUrl

from app.core.bootstrap import bootstrap_runtime_state
from app.core.runtime_state import RuntimeState
from app.core.settings import APP_ENV_DEVELOPMENT, APP_ENV_PRODUCTION, Settings

_PLAINTEXT_WARNING = "BASE_URL uses plain HTTP"


def _settings(**overrides: Any) -> Settings:
    return Settings(pairing_backend_url="", **overrides)


class TestPlaintextBaseUrlWarning:
    """Tests for bootstrap_runtime_state() warnings about plaintext BASE_URL in production."""

    def test_warns_on_http_base_url_in_production(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that a warning is emitted when BASE_URL is HTTP in production mode."""
        app_settings = _settings(base_url=HttpUrl("http://camera.example:8018"), app_env=APP_ENV_PRODUCTION)
        with caplog.at_level(logging.WARNING):
            bootstrap_runtime_state(RuntimeState(), app_settings)
        assert _PLAINTEXT_WARNING in caplog.text

    def test_silent_on_https_base_url(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that no warning is emitted for HTTPS base URLs."""
        app_settings = _settings(base_url=HttpUrl("https://camera.example"), app_env=APP_ENV_PRODUCTION)
        with caplog.at_level(logging.WARNING):
            bootstrap_runtime_state(RuntimeState(), app_settings)
        assert _PLAINTEXT_WARNING not in caplog.text

    def test_silent_on_http_in_development(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that no warning is emitted for HTTP base URLs in development mode."""
        app_settings = _settings(base_url=HttpUrl("http://camera.example:8018"), app_env=APP_ENV_DEVELOPMENT)
        with caplog.at_level(logging.WARNING):
            bootstrap_runtime_state(RuntimeState(), app_settings)
        assert _PLAINTEXT_WARNING not in caplog.text
