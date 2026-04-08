"""Tests for relay-related config behavior."""

import json
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings, apply_relay_credentials

RELAY_BACKEND_URL = "wss://example.com/ws"
RELAY_CAMERA_ID = "cam-1"
RELAY_API_KEY = "key-1"


class TestRelayEnabledProperty:
    """Test for the  `Settings.relay_enabled` property."""

    def test_disabled_by_default(self) -> None:
        """Relay should be disabled if no fields are set."""
        s = Settings()
        assert s.relay_enabled is False

    def test_enabled_when_all_fields_set(self) -> None:
        """Relay should be enabled if all required fields are set."""
        s = Settings(
            relay_backend_url="wss://example.com/ws",
            relay_camera_id="cam-1",
            relay_api_key="key-1",
        )
        assert s.relay_enabled is True

    def test_disabled_when_partial(self) -> None:
        """Relay should be disabled if only some fields are set."""
        s = Settings(relay_backend_url="wss://example.com/ws")
        assert s.relay_enabled is False

    def test_disabled_when_only_key(self) -> None:
        """Relay should be disabled if only the API key is set."""
        s = Settings(relay_api_key="key-1")
        assert s.relay_enabled is False


class TestApplyRelayCredentials:
    """Tests for the `apply_relay_credentials` function."""

    def test_loads_credentials_from_file(self, tmp_path: Path) -> None:
        """Should load credentials from the file and apply to settings."""
        creds = {
            "relay_backend_url": RELAY_BACKEND_URL,
            "relay_camera_id": RELAY_CAMERA_ID,
            "relay_api_key": RELAY_API_KEY,
        }
        creds_file = tmp_path / "relay_credentials.json"
        creds_file.write_text(json.dumps(creds))

        with (
            patch("app.utils.pairing._CREDENTIALS_FILE", creds_file),
            patch("app.core.config.settings") as mock_settings,
        ):
            mock_settings.relay_backend_url = ""
            mock_settings.relay_camera_id = ""
            mock_settings.relay_api_key = ""
            mock_settings.authorized_api_keys = []
            apply_relay_credentials()
            assert mock_settings.relay_backend_url == RELAY_BACKEND_URL
            assert mock_settings.relay_camera_id == RELAY_CAMERA_ID
            assert mock_settings.relay_api_key == RELAY_API_KEY
            assert RELAY_API_KEY in mock_settings.authorized_api_keys

    def test_noop_when_no_file(self, tmp_path: Path) -> None:
        """Should do nothing if the credentials file doesn't exist."""
        creds_file = tmp_path / "relay_credentials.json"
        with (
            patch("app.utils.pairing._CREDENTIALS_FILE", creds_file),
            patch("app.core.config.settings") as mock_settings,
        ):
            mock_settings.relay_backend_url = ""
            mock_settings.relay_api_key = ""
            mock_settings.authorized_api_keys = []
            apply_relay_credentials()
            assert mock_settings.relay_backend_url == ""
