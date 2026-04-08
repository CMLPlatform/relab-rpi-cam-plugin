"""Tests for configuration and settings validation."""

import pytest

from app.core.config import Settings

WSS_URL = "wss://example.com/ws"
WS_URL = "ws://example.com/ws"
HTTP_URL = "http://example.com"
HTTPS_URL = "https://example.com"


class TestRelayUrlValidation:
    """Tests for the relay_backend_url field validator."""

    def test_empty_url_is_allowed(self) -> None:
        """Should allow an empty string for relay_backend_url."""
        s = Settings(relay_backend_url="")
        assert s.relay_backend_url == ""

    def test_wss_scheme_is_accepted(self) -> None:
        """Should accept URLs with the wss:// scheme."""
        s = Settings(relay_backend_url=WSS_URL)
        assert s.relay_backend_url == WSS_URL

    def test_ws_scheme_is_accepted_with_warning(self) -> None:
        """Should accept ws:// URLs but emit a warning about unencrypted connections."""
        with pytest.warns(UserWarning, match="unencrypted ws://"):
            s = Settings(relay_backend_url=WS_URL)
        assert s.relay_backend_url == WS_URL

    def test_http_scheme_is_rejected(self) -> None:
        """Should reject URLs with the http:// scheme since it's not secure for WebSocket connections."""
        with pytest.raises(ValueError, match="wss://"):
            Settings(relay_backend_url=HTTP_URL)

    def test_https_scheme_is_rejected(self) -> None:
        """Should reject URLs with the https:// scheme since it's not valid for WebSocket connections."""
        with pytest.raises(ValueError, match="wss://"):
            Settings(relay_backend_url=HTTPS_URL)


class TestSettingsDefaults:
    """Tests for sensible default configuration values."""

    def test_default_camera_device(self) -> None:
        """Should default to camera device 0 if not specified.

        This is typically the built-in camera on a Raspberry Pi.
        """
        s = Settings()
        assert s.camera_device_num == 0

    def test_default_relay_disabled(self) -> None:
        """By default, the relay should be disabled since it requires explicit configuration to work securely."""
        s = Settings()
        assert s.relay_enabled is False
