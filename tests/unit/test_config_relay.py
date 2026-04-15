"""Tests for relay-related config behavior."""

import json
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings, apply_relay_credentials
from tests.constants import EXAMPLE_RELAY_BACKEND_URL

RELAY_CAMERA_ID = "cam-1"
RELAY_AUTH_SCHEME = "device_assertion"
RELAY_KEY_ID = "key-1"
RELAY_PRIVATE_KEY_PEM = "private-key"


class TestRelayEnabledProperty:
    """Test for the  `Settings.relay_enabled` property."""

    def test_disabled_by_default(self) -> None:
        """Relay should be disabled if no fields are set."""
        s = Settings()
        assert s.relay_enabled is False

    def test_enabled_when_all_fields_set(self) -> None:
        """Relay should be enabled if all required fields are set."""
        s = Settings(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )
        assert s.relay_enabled is True

    def test_disabled_when_partial(self) -> None:
        """Relay should be disabled if only some fields are set."""
        s = Settings(relay_backend_url=EXAMPLE_RELAY_BACKEND_URL)
        assert s.relay_enabled is False

    def test_disabled_when_only_key(self) -> None:
        """Relay should be disabled if only the private key is set."""
        s = Settings(relay_private_key_pem=RELAY_PRIVATE_KEY_PEM)
        assert s.relay_enabled is False


class TestApplyRelayCredentials:
    """Tests for the `apply_relay_credentials` function."""

    def test_loads_credentials_from_file(self, tmp_path: Path) -> None:
        """Should load credentials from the file and apply to settings."""
        creds = {
            "relay_backend_url": EXAMPLE_RELAY_BACKEND_URL,
            "relay_camera_id": RELAY_CAMERA_ID,
            "relay_auth_scheme": RELAY_AUTH_SCHEME,
            "relay_key_id": RELAY_KEY_ID,
            "relay_private_key_pem": RELAY_PRIVATE_KEY_PEM,
        }
        creds_file = tmp_path / "relay_credentials.json"
        creds_file.write_text(json.dumps(creds))

        with (
            patch("app.utils.pairing._CREDENTIALS_FILE", creds_file),
            patch("app.core.config.settings") as mock_settings,
            patch("app.api.dependencies.auth.reload_authorized_hashes"),
        ):
            mock_settings.relay_backend_url = ""
            mock_settings.relay_camera_id = ""
            mock_settings.relay_auth_scheme = ""
            mock_settings.relay_key_id = ""
            mock_settings.relay_private_key_pem = ""
            mock_settings.local_relay_api_key = ""
            mock_settings.authorized_api_keys = []
            apply_relay_credentials()
            assert mock_settings.relay_backend_url == EXAMPLE_RELAY_BACKEND_URL
            assert mock_settings.relay_camera_id == RELAY_CAMERA_ID
            assert mock_settings.relay_auth_scheme == RELAY_AUTH_SCHEME
            assert mock_settings.relay_key_id == RELAY_KEY_ID
            assert mock_settings.relay_private_key_pem == RELAY_PRIVATE_KEY_PEM
            assert mock_settings.local_relay_api_key.startswith("LOCAL_")
            assert mock_settings.local_relay_api_key in mock_settings.authorized_api_keys

    def test_noop_when_no_file(self, tmp_path: Path) -> None:
        """Should do nothing if the credentials file doesn't exist."""
        creds_file = tmp_path / "relay_credentials.json"
        with (
            patch("app.utils.pairing._CREDENTIALS_FILE", creds_file),
            patch("app.core.config.settings") as mock_settings,
            patch("app.api.dependencies.auth.reload_authorized_hashes"),
        ):
            mock_settings.relay_backend_url = ""
            mock_settings.relay_private_key_pem = ""
            mock_settings.authorized_api_keys = []
            apply_relay_credentials()
            assert mock_settings.relay_backend_url == ""
