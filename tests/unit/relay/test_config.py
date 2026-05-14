"""Tests for relay-related config behavior."""

from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.bootstrap import apply_relay_credentials, set_runtime_relay_credentials
from app.core.runtime_state import RuntimeState
from app.core.settings import Settings
from tests.constants import EXAMPLE_RELAY_BACKEND_URL
from tests.support.fakes import fresh_p256_pem

RELAY_CAMERA_ID = "cam-1"
RELAY_AUTH_SCHEME = "device_assertion"
RELAY_KEY_ID = "key-1"
ENV_RELAY_BACKEND_URL = "wss://env-backend/ws/connect"
ENV_RELAY_CAMERA_ID = "env-cam"
ENV_RELAY_KEY_ID = "env-key"


def _fresh_rsa_pem() -> str:
    """Mint a throwaway RSA private key in PEM form."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


RELAY_PRIVATE_KEY_PEM = fresh_p256_pem()
ENV_RELAY_PRIVATE_KEY_PEM = fresh_p256_pem()


class TestStaticRelayCredentialsProperty:
    """Test the static relay bootstrap credential helper."""

    def test_disabled_by_default(self) -> None:
        """Relay should be disabled if no fields are set."""
        s = Settings()
        assert s.has_static_relay_credentials is False

    def test_enabled_when_all_fields_set(self) -> None:
        """Relay should be enabled if all required fields are set."""
        s = Settings(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )
        assert s.has_static_relay_credentials is True

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


class TestSetRuntimeRelayCredentials:
    """Tests for relay signing credential validation at the runtime boundary."""

    def test_accepts_valid_device_assertion_credentials(self) -> None:
        """Valid device assertion credentials should be applied to runtime state."""
        runtime_state = RuntimeState()

        set_runtime_relay_credentials(
            runtime_state=runtime_state,
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id=RELAY_CAMERA_ID,
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )

        assert runtime_state.relay_enabled is True
        assert runtime_state.relay_camera_id == RELAY_CAMERA_ID
        assert runtime_state.relay_key_id == RELAY_KEY_ID

    def test_rejects_non_device_assertion_auth_scheme(self) -> None:
        """Relay JWT signing credentials should only support device assertions."""
        with pytest.raises(ValueError, match="device_assertion"):
            set_runtime_relay_credentials(
                runtime_state=RuntimeState(),
                relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
                relay_camera_id=RELAY_CAMERA_ID,
                relay_auth_scheme="bearer",
                relay_key_id=RELAY_KEY_ID,
                relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
            )

    @pytest.mark.parametrize(
        ("field_name", "relay_camera_id", "relay_key_id"),
        [
            ("relay_camera_id", "bad camera id", RELAY_KEY_ID),
            ("relay_key_id", RELAY_CAMERA_ID, "bad key id!"),
        ],
    )
    def test_rejects_malformed_relay_identifiers(
        self,
        field_name: str,
        relay_camera_id: str,
        relay_key_id: str,
    ) -> None:
        """Relay camera and key identifiers should stay bounded and URL-safe."""
        with pytest.raises(ValueError, match=field_name):
            set_runtime_relay_credentials(
                runtime_state=RuntimeState(),
                relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
                relay_camera_id=relay_camera_id,
                relay_auth_scheme=RELAY_AUTH_SCHEME,
                relay_key_id=relay_key_id,
                relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
            )

    def test_rejects_non_p256_private_key(self) -> None:
        """Device assertions must be signed with an EC P-256 private key."""
        with pytest.raises(ValueError, match="P-256"):
            set_runtime_relay_credentials(
                runtime_state=RuntimeState(),
                relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
                relay_camera_id=RELAY_CAMERA_ID,
                relay_auth_scheme=RELAY_AUTH_SCHEME,
                relay_key_id=RELAY_KEY_ID,
                relay_private_key_pem=_fresh_rsa_pem(),
            )

    def test_rejects_invalid_private_key_pem(self) -> None:
        """Malformed private key material should fail before runtime state changes."""
        with pytest.raises(ValueError, match="private key"):
            set_runtime_relay_credentials(
                runtime_state=RuntimeState(),
                relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
                relay_camera_id=RELAY_CAMERA_ID,
                relay_auth_scheme=RELAY_AUTH_SCHEME,
                relay_key_id=RELAY_KEY_ID,
                relay_private_key_pem="not a private key",
            )


class TestAuthorizedApiKeysMutation:
    """Tests for atomic authorized-key updates."""

    def test_add_authorized_api_key_rebinds_with_deduplicated_list(self) -> None:
        """Adding a key should replace the snapshot instead of mutating in place."""
        runtime_state = RuntimeState(authorized_api_keys=frozenset({"one", "two"}))
        original_keys = runtime_state.authorized_api_keys

        runtime_state.add_authorized_api_key("three")

        assert runtime_state.authorized_api_keys == frozenset({"one", "two", "three"})
        assert runtime_state.authorized_api_keys is not original_keys

    def test_add_authorized_api_key_skips_existing_key(self) -> None:
        """Existing keys should not trigger a snapshot replacement."""
        runtime_state = RuntimeState(authorized_api_keys=frozenset({"one", "two"}))
        original_keys = runtime_state.authorized_api_keys

        runtime_state.add_authorized_api_key("two")

        assert runtime_state.authorized_api_keys is original_keys
