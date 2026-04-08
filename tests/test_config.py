"""Tests for configuration and settings validation."""

import pytest

from app.core.config import Settings


class TestRelayUrlValidation:
    """Tests for the relay_backend_url field validator."""

    def test_empty_url_is_allowed(self) -> None:
        s = Settings(relay_backend_url="")
        assert s.relay_backend_url == ""

    def test_wss_scheme_is_accepted(self) -> None:
        s = Settings(relay_backend_url="wss://example.com/ws")
        assert s.relay_backend_url == "wss://example.com/ws"

    def test_ws_scheme_is_accepted_with_warning(self) -> None:
        with pytest.warns(UserWarning, match="unencrypted ws://"):
            s = Settings(relay_backend_url="ws://example.com/ws")
        assert s.relay_backend_url == "ws://example.com/ws"

    def test_http_scheme_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="wss://"):
            Settings(relay_backend_url="http://example.com")

    def test_https_scheme_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="wss://"):
            Settings(relay_backend_url="https://example.com")


class TestSettingsDefaults:
    """Tests for sensible default configuration values."""

    def test_default_camera_device(self) -> None:
        s = Settings()
        assert s.camera_device_num == 0

    def test_default_relay_disabled(self) -> None:
        s = Settings()
        assert s.relay_enabled is False

    def test_default_hls_manifest(self) -> None:
        s = Settings()
        assert s.hls_manifest_filename == "master.m3u8"
