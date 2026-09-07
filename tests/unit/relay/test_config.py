"""Tests for relay-related config behavior."""

from unittest.mock import patch

import pytest

from app.core.bootstrap import apply_relay_credentials, set_runtime_relay_credentials
from app.core.runtime_state import RuntimeState
from app.core.settings import (
    APP_ENV_DEVELOPMENT,
    APP_ENV_PRODUCTION,
    RelayUrlError,
    Settings,
    settings,
    validate_relay_url_origin,
)
from tests.constants import EXAMPLE_BACKEND_URL, EXAMPLE_RELAY_BACKEND_URL
from tests.fakes import fresh_p256_pem

RELAY_CAMERA_ID = "cam-1"
RELAY_AUTH_SCHEME = "device_assertion"
RELAY_KEY_ID = "key-1"
ENV_RELAY_BACKEND_URL = "wss://env-backend/ws/connect"
ENV_RELAY_CAMERA_ID = "env-cam"
ENV_RELAY_KEY_ID = "env-key"


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

    @pytest.fixture(autouse=True)
    def _pair_with_the_example_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Relay URLs in these tests belong to the backend the device paired with."""
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)

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
            patch("app.auth.dependencies.reload_authorized_keys"),
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

    @pytest.fixture(autouse=True)
    def _pair_with_the_example_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Relay URLs in these tests belong to the backend the device paired with."""
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)

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
        """Credential validation should be wired into the runtime boundary."""
        # Detailed validation cases live in tests/unit/relay/test_credentials.py;
        # this guards that set_runtime_relay_credentials actually invokes them.
        with pytest.raises(ValueError, match="device_assertion"):
            set_runtime_relay_credentials(
                runtime_state=RuntimeState(),
                relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
                relay_camera_id=RELAY_CAMERA_ID,
                relay_auth_scheme="bearer",
                relay_key_id=RELAY_KEY_ID,
                relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
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


FOREIGN_RELAY_BACKEND_URL = "wss://attacker.example/v1/plugins/rpi-cam/ws/connect"
LOOPBACK_BACKEND_URL = "http://127.0.0.1:8000"


class TestValidateRelayUrlOrigin:
    """Tests for the relay host pin applied to backend-supplied relay URLs."""

    def test_accepts_the_paired_host_on_another_port(self) -> None:
        """A different port on the paired host still reaches the same operator."""
        value = "wss://example.com:8443/v1/plugins/rpi-cam/ws/connect"
        assert (
            validate_relay_url_origin(value, pairing_backend_url=EXAMPLE_BACKEND_URL, app_env=APP_ENV_PRODUCTION)
            == value
        )

    def test_rejects_a_foreign_host(self) -> None:
        """A relay URL naming another host must never receive this device's assertion."""
        with pytest.raises(RelayUrlError, match="not the backend this device paired with"):
            validate_relay_url_origin(
                FOREIGN_RELAY_BACKEND_URL,
                pairing_backend_url=EXAMPLE_BACKEND_URL,
                app_env=APP_ENV_PRODUCTION,
            )

    def test_rejects_a_foreign_host_in_development_against_a_remote_backend(self) -> None:
        """Development only relaxes the pin for a loopback backend, not a remote one."""
        with pytest.raises(RelayUrlError, match="not the backend this device paired with"):
            validate_relay_url_origin(
                FOREIGN_RELAY_BACKEND_URL,
                pairing_backend_url=EXAMPLE_BACKEND_URL,
                app_env=APP_ENV_DEVELOPMENT,
            )

    def test_relaxes_the_pin_for_a_loopback_backend_in_development(self) -> None:
        """The container rewrite legitimately moves a loopback backend to another host."""
        value = "ws://host.docker.internal:8000/v1/plugins/rpi-cam/ws/connect"
        assert (
            validate_relay_url_origin(value, pairing_backend_url=LOOPBACK_BACKEND_URL, app_env=APP_ENV_DEVELOPMENT)
            == value
        )

    def test_accepts_an_empty_relay_url(self) -> None:
        """An unset relay URL is not this validator's concern."""
        assert validate_relay_url_origin("", pairing_backend_url=EXAMPLE_BACKEND_URL, app_env=APP_ENV_PRODUCTION) == ""


class TestPersistedRelayCredentialsOrigin:
    """The pin must also cover credentials restored from disk, not just fresh pairings."""

    def test_rejects_a_persisted_relay_host_the_device_never_paired_with(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A foreign host in the credentials file leaves the relay disabled, not the app dead."""
        monkeypatch.setattr(settings, "app_env", APP_ENV_PRODUCTION)
        monkeypatch.setattr(settings, "pairing_backend_url", EXAMPLE_BACKEND_URL)
        creds = {
            "relay_backend_url": FOREIGN_RELAY_BACKEND_URL,
            "relay_camera_id": RELAY_CAMERA_ID,
            "relay_auth_scheme": RELAY_AUTH_SCHEME,
            "relay_key_id": RELAY_KEY_ID,
            "relay_private_key_pem": RELAY_PRIVATE_KEY_PEM,
        }
        runtime_state = RuntimeState()

        with patch("app.core.bootstrap.load_relay_credentials", return_value=creds):
            apply_relay_credentials(runtime_state)

        assert runtime_state.relay_backend_url == ""
        assert not runtime_state.relay_enabled
