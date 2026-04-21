"""Tests for relay-related config behavior."""

from unittest.mock import patch

import pytest

from app.core.bootstrap import _add_authorized_api_key, apply_relay_credentials
from app.core.config import Settings
from app.core.runtime_state import RuntimeState
from tests.constants import EXAMPLE_RELAY_BACKEND_URL

RELAY_CAMERA_ID = "cam-1"
RELAY_AUTH_SCHEME = "device_assertion"
RELAY_KEY_ID = "key-1"
RELAY_PRIVATE_KEY_PEM = "private-key"
ENV_RELAY_BACKEND_URL = "wss://env-backend/ws/connect"
ENV_RELAY_CAMERA_ID = "env-cam"
ENV_RELAY_KEY_ID = "env-key"
ENV_RELAY_PRIVATE_KEY_PEM = "env-private-key"


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
        """Partial relay bootstrap config should be rejected."""
        with pytest.raises(ValueError, match="Relay bootstrap config must set"):
            Settings(relay_backend_url=EXAMPLE_RELAY_BACKEND_URL)

    def test_disabled_when_only_key(self) -> None:
        """Relay bootstrap config should reject lone private-key setup."""
        with pytest.raises(ValueError, match="Relay bootstrap config must set"):
            Settings(relay_private_key_pem=RELAY_PRIVATE_KEY_PEM)


class TestApplyRelayCredentials:
    """Tests for the `apply_relay_credentials` function."""

    def test_loads_credentials_from_file(self) -> None:
        """Should load credentials from the file and apply to runtime state."""
        creds = {
            "relay_backend_url": EXAMPLE_RELAY_BACKEND_URL,
            "relay_camera_id": RELAY_CAMERA_ID,
            "relay_auth_scheme": RELAY_AUTH_SCHEME,
            "relay_key_id": RELAY_KEY_ID,
            "relay_private_key_pem": RELAY_PRIVATE_KEY_PEM,
        }
        runtime_state = RuntimeState()

        with patch("app.core.bootstrap.load_relay_credentials", return_value=creds):
            apply_relay_credentials(runtime_state)

        assert runtime_state.relay_backend_url == EXAMPLE_RELAY_BACKEND_URL
        assert runtime_state.relay_camera_id == RELAY_CAMERA_ID
        assert runtime_state.relay_auth_scheme == RELAY_AUTH_SCHEME
        assert runtime_state.relay_key_id == RELAY_KEY_ID
        assert runtime_state.relay_private_key_pem == RELAY_PRIVATE_KEY_PEM
        assert runtime_state.local_relay_api_key.startswith("LOCAL_")
        assert runtime_state.local_relay_api_key in runtime_state.authorized_api_keys

    def test_noop_when_no_file(self) -> None:
        """Should do nothing if the credentials file doesn't exist."""
        runtime_state = RuntimeState()
        with (
            patch("app.core.bootstrap.load_relay_credentials", return_value={}),
            patch("app.auth.dependencies.reload_authorized_hashes"),
        ):
            apply_relay_credentials(runtime_state)
        assert runtime_state.relay_backend_url == ""

    def test_keeps_env_bootstrap_credentials_when_runtime_already_configured(self) -> None:
        """Env/bootstrap relay config should win over persisted credentials."""
        runtime_state = RuntimeState(
            relay_backend_url=ENV_RELAY_BACKEND_URL,
            relay_camera_id=ENV_RELAY_CAMERA_ID,
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=ENV_RELAY_KEY_ID,
            relay_private_key_pem=ENV_RELAY_PRIVATE_KEY_PEM,
        )
        creds = {
            "relay_backend_url": EXAMPLE_RELAY_BACKEND_URL,
            "relay_camera_id": RELAY_CAMERA_ID,
            "relay_auth_scheme": RELAY_AUTH_SCHEME,
            "relay_key_id": RELAY_KEY_ID,
            "relay_private_key_pem": RELAY_PRIVATE_KEY_PEM,
        }

        with patch("app.core.bootstrap.load_relay_credentials", return_value=creds):
            apply_relay_credentials(runtime_state)

        assert runtime_state.relay_backend_url == ENV_RELAY_BACKEND_URL
        assert runtime_state.relay_camera_id == ENV_RELAY_CAMERA_ID
        assert runtime_state.relay_key_id == ENV_RELAY_KEY_ID
        assert runtime_state.relay_private_key_pem == ENV_RELAY_PRIVATE_KEY_PEM


class TestAuthorizedApiKeysMutation:
    """Tests for atomic authorized-key updates."""

    def test_add_authorized_api_key_rebinds_with_deduplicated_list(self) -> None:
        """Adding a key should replace the snapshot instead of mutating in place."""
        runtime_state = RuntimeState(authorized_api_keys=frozenset({"one", "two"}))
        original_keys = runtime_state.authorized_api_keys

        _add_authorized_api_key(runtime_state, "three")

        assert runtime_state.authorized_api_keys == frozenset({"one", "two", "three"})
        assert runtime_state.authorized_api_keys is not original_keys

    def test_add_authorized_api_key_skips_existing_key(self) -> None:
        """Existing keys should not trigger a snapshot replacement."""
        runtime_state = RuntimeState(authorized_api_keys=frozenset({"one", "two"}))
        original_keys = runtime_state.authorized_api_keys

        _add_authorized_api_key(runtime_state, "two")

        assert runtime_state.authorized_api_keys is original_keys
