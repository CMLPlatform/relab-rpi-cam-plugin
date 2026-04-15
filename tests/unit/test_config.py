"""Tests for configuration and settings validation."""

import json
from pathlib import Path

import pytest
from pydantic import HttpUrl

import app.core.config as config_mod
from app.core.config import Settings
from app.core.runtime_state import RuntimeState
from tests.constants import (
    EXAMPLE_RELAY_BACKEND_URL,
    EXAMPLE_RELAY_BACKEND_URL_UNSECURE,
    EXAMPLE_RELAY_HTTP_URL,
    EXAMPLE_RELAY_HTTPS_URL,
)

OTEL_SERVICE_NAME = "relab-rpi-cam-plugin"
EXPLICIT_S3 = "s3"
EXPLICIT_BACKEND = "backend"
UNCONFIGURED = "unconfigured"
RUNTIME_KEY = "runtime-key"
STORED_KEY = "stored-key"
GENERATED_LOCAL_KEY = "local_generated-token"
LOCAL_MODE_DISABLED_WARNING = "LOCAL_MODE_ENABLED=false but a local API key exists"
PAIRING_LOOPBACK_WARNING = "PAIRING_BACKEND_URL uses loopback inside a container"
TEST_S3_VALUE = "s3-test-value"


class TestRelayUrlValidation:
    """Tests for the relay_backend_url field validator."""

    def test_empty_url_is_allowed(self) -> None:
        """Should allow an empty string for relay_backend_url."""
        s = Settings(relay_backend_url="")
        assert s.relay_backend_url == ""

    def test_wss_scheme_is_accepted(self) -> None:
        """Should accept URLs with the wss:// scheme."""
        s = Settings(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_key_id="key-1",
            relay_private_key_pem="pem",
        )
        assert s.relay_backend_url == EXAMPLE_RELAY_BACKEND_URL

    def test_ws_scheme_is_accepted_with_warning(self) -> None:
        """Should accept ws:// URLs but emit a warning about unencrypted connections."""
        with pytest.warns(UserWarning, match="unencrypted ws://"):
            s = Settings(
                relay_backend_url=EXAMPLE_RELAY_BACKEND_URL_UNSECURE,
                relay_camera_id="cam-1",
                relay_key_id="key-1",
                relay_private_key_pem="pem",
            )
        assert s.relay_backend_url == EXAMPLE_RELAY_BACKEND_URL_UNSECURE

    def test_http_scheme_is_rejected(self) -> None:
        """Should reject URLs with the http:// scheme since it's not secure for WebSocket connections."""
        with pytest.raises(ValueError, match="wss://"):
            Settings(relay_backend_url=EXAMPLE_RELAY_HTTP_URL)

    def test_https_scheme_is_rejected(self) -> None:
        """Should reject URLs with the https:// scheme since it's not valid for WebSocket connections."""
        with pytest.raises(ValueError, match="wss://"):
            Settings(relay_backend_url=EXAMPLE_RELAY_HTTPS_URL)

    def test_partial_relay_bootstrap_config_is_rejected(self) -> None:
        """Relay bootstrap config should be all-or-nothing."""
        with pytest.raises(ValueError, match="Relay bootstrap config must set"):
            Settings(relay_backend_url=EXAMPLE_RELAY_BACKEND_URL, relay_camera_id="cam-1")

    def test_non_device_assertion_relay_scheme_is_rejected(self) -> None:
        """Relay bootstrap config only supports device assertions."""
        with pytest.raises(ValueError, match="RELAY_AUTH_SCHEME must be device_assertion"):
            Settings(
                relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
                relay_camera_id="cam-1",
                relay_auth_scheme="bearer",
                relay_key_id="key-1",
                relay_private_key_pem="pem",
            )


class TestAuthorizedApiKeysValidation:
    """Tests for the authorized_api_keys field validator."""

    def test_valid_json_array(self) -> None:
        """Should accept a properly JSON-encoded list of keys."""
        s = Settings.model_validate({"authorized_api_keys": '["key1", "key2"]'})
        assert s.authorized_api_keys == ["key1", "key2"]

    def test_empty_string_returns_empty_list(self) -> None:
        """An empty env var should produce an empty list without raising."""
        s = Settings.model_validate({"authorized_api_keys": ""})
        assert s.authorized_api_keys == []

    def test_unquoted_json_array_falls_back_to_comma_split(self) -> None:
        """[KEY] (unquoted string) — common .env mistake — should not crash."""
        s = Settings.model_validate({"authorized_api_keys": "[CHANGE_ME]"})
        assert s.authorized_api_keys == ["CHANGE_ME"]

    def test_comma_separated_string(self) -> None:
        """Comma-separated values without brackets should also be accepted."""
        s = Settings.model_validate({"authorized_api_keys": "key1, key2, key3"})
        assert s.authorized_api_keys == ["key1", "key2", "key3"]

    def test_single_key_string(self) -> None:
        """A single key without brackets or commas should be wrapped in a list."""
        s = Settings.model_validate({"authorized_api_keys": '["only-key"]'})
        assert s.authorized_api_keys == ["only-key"]

    def test_list_passthrough(self) -> None:
        """A Python list passed directly should be returned unchanged."""
        s = Settings(authorized_api_keys=["a", "b"])
        assert s.authorized_api_keys == ["a", "b"]

    def test_iterable_passthrough(self) -> None:
        """Non-string iterables should be coerced into lists."""
        s = Settings.model_validate({"authorized_api_keys": ("a", "b")})
        assert s.authorized_api_keys == ["a", "b"]


class TestLocalOriginsValidation:
    """Tests for local direct-connection origin parsing."""

    def test_local_allowed_origins_accepts_json_list(self) -> None:
        """JSON arrays should be parsed into origin lists."""
        s = Settings.model_validate({"local_allowed_origins": '["http://a", "http://b"]'})
        assert s.local_allowed_origins == ["http://a", "http://b"]

    def test_local_allowed_origins_accepts_comma_separated_values(self) -> None:
        """Comma-separated input should stay easy to use in env files."""
        s = Settings.model_validate({"local_allowed_origins": "http://a, http://b"})
        assert s.local_allowed_origins == ["http://a", "http://b"]

    def test_local_allowed_origins_accepts_iterables(self) -> None:
        """Non-string iterables should also be accepted."""
        s = Settings.model_validate({"local_allowed_origins": ("http://a", "http://b")})
        assert s.local_allowed_origins == ["http://a", "http://b"]


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

    def test_observability_disabled_by_default(self) -> None:
        """Tracing should stay opt-in by default."""
        s = Settings()
        assert s.otel_enabled is False
        assert s.otel_service_name == OTEL_SERVICE_NAME
        assert s.otel_exporter_otlp_endpoint == ""

    def test_cookie_secure_defaults_to_https_scheme(self) -> None:
        """HTTPS base URLs should secure cookies by default."""
        s = Settings(base_url=HttpUrl("https://camera.example"))
        assert s.cookie_secure is True

    def test_cookie_secure_respects_explicit_override(self) -> None:
        """Explicit cookie security settings should win over the base URL."""
        s = Settings(base_url=HttpUrl("http://camera.example"), auth_cookie_secure=True)
        assert s.cookie_secure is True

    @pytest.mark.parametrize(
        ("raw_value", "expected_value"),
        [
            (True, True),
            ("debug", True),
            ("production", False),
            ("unexpected", False),
            (1, True),
            (0, False),
        ],
    )
    def test_debug_parser_handles_common_runtime_values(self, raw_value: object, expected_value: object) -> None:
        """Debug parsing should be resilient to common environment spellings."""
        s = Settings.model_validate({"debug": raw_value})
        assert s.debug is expected_value


class TestPairingSettings:
    """Tests for pairing-related settings defaults."""

    def test_defaults_are_sensible(self) -> None:
        """Pairing retry settings should default to short, readable delays."""
        s = Settings()
        assert s.pairing_register_timeout_retry_s == 1
        assert s.pairing_poll_interval_s == 3


class TestImageSinkValidation:
    """Tests for image sink validation rules."""

    def test_backend_sink_requires_pairing_backend_url(self) -> None:
        """Explicit backend sink config should require a backend URL."""
        with pytest.raises(ValueError, match="IMAGE_SINK=backend requires PAIRING_BACKEND_URL"):
            Settings(image_sink="backend", pairing_backend_url="")

    def test_s3_sink_requires_all_credentials(self) -> None:
        """Explicit S3 sink config should fail loudly on missing credentials."""
        with pytest.raises(ValueError, match="IMAGE_SINK=s3 requires"):
            Settings(image_sink="s3")


class TestConfigBootstrapHelpers:
    """Tests for runtime bootstrap helpers in config."""

    def test_is_running_in_container_checks_dockerenv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Container detection should be a thin /.dockerenv existence check."""
        monkeypatch.setattr("app.core.config.Path.exists", lambda _self: True)

        assert config_mod._is_running_in_container() is True

    def test_uses_loopback_host_detects_loopback_and_empty(self) -> None:
        """Loopback detection should stay narrow and predictable."""
        assert config_mod._uses_loopback_host("") is False
        assert config_mod._uses_loopback_host("http://localhost:8000") is True
        assert config_mod._uses_loopback_host("https://camera.example") is False

    def test_resolve_image_sink_choice_prefers_explicit_and_inferred_modes(self) -> None:
        """Image-sink resolution should cover explicit and auto-inferred cases."""
        explicit = Settings(
            image_sink="s3",
            s3_endpoint_url="https://s3.example",
            s3_bucket="bucket",
            s3_access_key_id="a",
            s3_secret_access_key=TEST_S3_VALUE,
        )
        auto_s3 = Settings(s3_endpoint_url="https://s3.example")
        auto_backend = Settings(pairing_backend_url="https://api.example")
        auto_unconfigured = Settings(pairing_backend_url="")

        assert config_mod.resolve_image_sink_choice(explicit) == EXPLICIT_S3
        assert config_mod.resolve_image_sink_choice(auto_s3) == EXPLICIT_S3
        assert config_mod.resolve_image_sink_choice(auto_backend) == EXPLICIT_BACKEND
        assert config_mod.resolve_image_sink_choice(auto_unconfigured) == UNCONFIGURED

    def test_persist_local_api_key_merges_existing_credentials(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Persisting the local key should merge with existing relay credentials."""
        credentials_file = tmp_path / "relay_credentials.json"
        credentials_file.write_text(json.dumps({"relay_camera_id": "cam-1"}))
        monkeypatch.setattr(config_mod, "_CREDENTIALS_FILE", credentials_file)

        config_mod._persist_local_api_key("local-key")

        assert json.loads(credentials_file.read_text()) == {
            "relay_camera_id": "cam-1",
            "local_api_key": "local-key",
        }

    def test_apply_local_mode_uses_existing_runtime_key(self) -> None:
        """An already-loaded runtime key should be reused without file reads."""
        runtime_state = RuntimeState(local_api_key=RUNTIME_KEY)
        app_settings = Settings(local_mode_enabled=True)

        config_mod.apply_local_mode(runtime_state, app_settings)

        assert runtime_state.local_api_key == RUNTIME_KEY
        assert runtime_state.authorized_api_keys == frozenset({RUNTIME_KEY})

    def test_apply_local_mode_loads_key_from_credentials_when_needed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Credentials-file keys should be used before generating a new one."""
        runtime_state = RuntimeState()
        app_settings = Settings(local_mode_enabled=False)
        monkeypatch.setattr(config_mod, "load_relay_credentials", lambda: {"local_api_key": STORED_KEY})

        config_mod.apply_local_mode(runtime_state, app_settings)

        assert runtime_state.local_api_key == STORED_KEY
        assert STORED_KEY not in runtime_state.authorized_api_keys

    def test_apply_local_mode_generates_and_persists_new_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing local keys should be generated once and persisted."""
        runtime_state = RuntimeState()
        app_settings = Settings(local_mode_enabled=True)
        persisted: list[str] = []
        monkeypatch.setattr(config_mod, "load_relay_credentials", lambda: None)
        monkeypatch.setattr(config_mod.secrets, "token_urlsafe", lambda _n: "generated-token")
        monkeypatch.setattr(config_mod, "_persist_local_api_key", persisted.append)

        config_mod.apply_local_mode(runtime_state, app_settings)

        assert runtime_state.local_api_key == GENERATED_LOCAL_KEY
        assert runtime_state.authorized_api_keys == frozenset({GENERATED_LOCAL_KEY})
        assert persisted == [GENERATED_LOCAL_KEY]

    def test_set_and_clear_runtime_relay_credentials(self) -> None:
        """Relay helpers should write and clear runtime credentials deterministically."""
        runtime_state = RuntimeState(local_relay_api_key="relay-local-key")

        config_mod.set_runtime_relay_credentials(
            runtime_state,
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme="device_assertion",
            relay_key_id="key-1",
            relay_private_key_pem="pem",
        )
        assert runtime_state.relay_enabled is True
        assert runtime_state.authorized_api_keys == frozenset({"relay-local-key"})

        config_mod.clear_runtime_relay_credentials(runtime_state)
        assert runtime_state.relay_enabled is False

    def test_bootstrap_runtime_state_logs_warnings_for_disabled_local_mode_and_loopback_pairing_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Bootstrap should emit the operator-facing warnings for risky-but-valid setups."""
        runtime_state = RuntimeState(local_api_key="existing-key")
        app_settings = Settings(local_mode_enabled=False, pairing_backend_url="http://localhost:8000")
        monkeypatch.setattr(config_mod, "apply_relay_credentials", lambda _state: None)
        monkeypatch.setattr(config_mod, "apply_local_mode", lambda _state, _settings: None)
        monkeypatch.setattr(config_mod, "_is_running_in_container", lambda: True)

        with caplog.at_level("WARNING"):
            config_mod.bootstrap_runtime_state(runtime_state, app_settings)

        assert LOCAL_MODE_DISABLED_WARNING in caplog.text
        assert PAIRING_LOOPBACK_WARNING in caplog.text
