"""Configuration settings for the Raspberry Pi API app."""

import json
import logging
import os
import secrets
import tempfile
import warnings
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

from pydantic import HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.runtime_state import RuntimeState
from app.utils.pairing import _CREDENTIALS_FILE, load_relay_credentials

# Set the project base directory and .env file
BASE_DIR: Path = (Path(__file__).resolve().parents[2]).resolve()
_HTTPS_SCHEME = "https"
_RELAY_AUTH_SCHEME_DEVICE_ASSERTION = "device_assertion"
_IMAGE_SINK_AUTO = "auto"
_IMAGE_SINK_BACKEND = "backend"
_IMAGE_SINK_S3 = "s3"
DEFAULT_PAIRING_BACKEND_URL = "https://api.cml-relab.org"
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Settings class to store all the configurations for the app."""

    base_url: HttpUrl = HttpUrl("http://127.0.0.1:8018/")  # Base URL for the Raspberry Pi server
    allowed_cors_origins: list[HttpUrl] = [  # Allowed origins for CORS. Must include the main API host
        HttpUrl("http://127.0.0.1:8000"),
        HttpUrl("http://localhost:8000"),
        HttpUrl("https://cml-relab.org"),
    ]
    authorized_api_keys: list[str] = []  # Bootstrap-only auth keys from env/.env
    camera_device_num: int = 0  # Camera device number (usually 0 or 1)

    # Initialize the settings configuration from the .env file
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # Directory paths
    image_path: Path = BASE_DIR / "data" / "images"  # Used for temporary storing of captured images
    templates_path: Path = BASE_DIR / "app" / "templates"  # Used for storing HTML templates
    static_path: Path = BASE_DIR / "app" / "static"  # Used for serving static files
    log_path: Path = BASE_DIR / "logs"  # Used for storing log files

    # Directory cleanup settings
    cleanup_interval_s: int = 60 * 10  # Interval for cleaning up expired files in seconds (10 minutes)
    image_ttl_s: int = 60 * 60  # Time-to-live for captured images in seconds (1 hour)
    max_stream_duration_s: int = 60 * 60 * 5  # Maximum duration for a stream in seconds (5 hours)
    check_stream_interval_s: int = 60  # Interval for checking stream duration in seconds (1 minute)
    check_stream_health_interval_s: int = 30  # Interval for checking stream health in seconds

    # Camera settings
    camera_backend: Literal["picamera2"] = "picamera2"

    # Preview pipeline settings
    # ``preview_hibernate_after_s = 0`` disables hibernation entirely (always-on).
    # Any positive value is a relay idle window in seconds — after no relay
    # traffic for that long, the lores preview encoder is stopped until a new
    # command arrives or the relay reconnects.
    preview_hibernate_after_s: int = 60 * 5  # Default: hibernate after 5 min idle

    # Image sink selection. ``auto`` infers from what's configured:
    # ``pairing_backend_url`` → backend, ``s3_endpoint_url`` → s3, nothing → error.
    image_sink: Literal["backend", "s3", "auto"] = "auto"
    # S3-compatible sink config (required when image_sink=s3 or when auto-
    # inferred from S3_ENDPOINT_URL). Works with MinIO, B2, R2, Wasabi, AWS S3.
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "us-east-1"
    # Template for the public URL returned to the frontend. Variables:
    # ``{endpoint}``, ``{bucket}``, ``{key}``. Default is path-style, which is
    # what MinIO + pre-signed S3 both return. Override for virtual-hosted S3
    # or CDN-fronted buckets (R2 custom domains, CloudFront, etc.).
    s3_public_url_template: str = "{endpoint}/{bucket}/{key}"

    # Auth
    auth_key_name: str = "X-API-Key"
    auth_cookie_secure: bool | None = None
    session_cookie_name: str = "relab_session"

    # Debug mode
    debug: bool = False

    # Observability / tracing
    otel_enabled: bool = False
    otel_service_name: str = "relab-rpi-cam-plugin"
    otel_exporter_otlp_endpoint: str = ""

    # Local (direct Ethernet / USB-C) mode
    # The plugin always auto-generates a persistent local_api_key on startup (saved to the
    # credentials JSON). The key is delivered to the frontend automatically via the relay's
    # /local-access-info endpoint — no manual copying required.
    # Set LOCAL_MODE_ENABLED=false to disable local API access entirely (opt-out).
    local_mode_enabled: bool = True
    local_api_key: str = ""  # Bootstrap-only local API key seed; runtime-owned after startup
    # Extra CORS origins allowed for direct-connect clients, e.g. "http://192.168.1.42"
    # Accepts a JSON array string or comma-separated list (same format as authorized_api_keys).
    local_allowed_origins: list[str] = []

    @field_validator("local_allowed_origins", mode="before")
    @classmethod
    def _parse_local_origins(cls, v: object) -> list[str]:
        """Accept a JSON array, comma-separated string, or empty value."""
        if isinstance(v, list):
            return cast("list[str]", v)
        if not isinstance(v, str):
            return cast("list[str]", list(v)) if isinstance(v, Iterable) else []
        stripped = v.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return [k.strip().strip("\"'") for k in stripped.strip("[]").split(",") if k.strip()]
        return cast("list[str]", [parsed] if not isinstance(parsed, list) else parsed)

    # WebSocket relay (auto-enabled when all three fields are set)
    relay_backend_url: str = ""  # wss://your-backend/plugins/rpi-cam/ws/connect
    relay_camera_id: str = ""
    relay_auth_scheme: str = "device_assertion"
    relay_key_id: str = ""
    relay_private_key_pem: str = ""
    local_relay_api_key: str = ""  # Bootstrap-only relay-local auth key seed; runtime-owned after startup

    @property
    def relay_enabled(self) -> bool:
        """Relay is enabled when the platform device credential is configured."""
        return bool(
            self.relay_backend_url
            and self.relay_camera_id
            and self.relay_auth_scheme == _RELAY_AUTH_SCHEME_DEVICE_ASSERTION
            and self.relay_key_id
            and self.relay_private_key_pem
        )

    @property
    def cookie_secure(self) -> bool:
        """Return whether auth cookies should be marked secure."""
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.base_url.scheme == _HTTPS_SCHEME

    # Pairing: set this to the backend's HTTP(S) API URL to enable zero-config pairing.
    # When set and relay credentials are absent, the RPi enters pairing mode on boot.
    pairing_backend_url: str = DEFAULT_PAIRING_BACKEND_URL
    pairing_register_timeout_retry_s: int = 1  # Delay before retrying a timed-out pairing register request
    pairing_poll_interval_s: int = 3  # Delay between pairing poll requests and after poll timeouts

    @field_validator("relay_backend_url")
    @classmethod
    def _validate_relay_url_scheme(cls, v: str) -> str:
        """Require a WebSocket scheme; warn loudly if not encrypted."""
        if not v:
            return v
        if not v.startswith(("wss://", "ws://")):
            msg = "relay_backend_url must use the wss:// (or ws://) scheme, not http/https"
            raise ValueError(msg)
        if v.startswith("ws://"):
            warnings.warn(
                "relay_backend_url uses unencrypted ws://. Switch to wss:// in production.",
                stacklevel=2,
            )
        return v

    @field_validator("authorized_api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls, v: object) -> list[str]:
        """Accept a JSON array, a comma-separated string, or an empty value.

        Handles common .env mistakes such as ``[KEY]`` (unquoted JSON string)
        by falling back to comma-splitting so the app still starts with a
        meaningful error rather than a cryptic JSONDecodeError.
        """
        if isinstance(v, list):
            return cast("list[str]", v)
        if not isinstance(v, str):
            return cast("list[str]", list(v)) if isinstance(v, Iterable) else []
        stripped = v.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            # Fall back to comma-separated: "key1, key2" or "[key1, key2]"
            return [k.strip().strip("\"'") for k in stripped.strip("[]").split(",") if k.strip()]
        return cast("list[str]", [parsed] if not isinstance(parsed, list) else parsed)

    @field_validator("debug", mode="before")
    @classmethod
    def _parse_debug(cls, v: object) -> bool:
        """Treat common truthy values as debug, everything else as off.

        This keeps settings initialization resilient in environments that set
        DEBUG to non-boolean values such as release labels.
        """
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            normalized = v.strip().lower()
            if normalized in {"1", "true", "yes", "on", "debug"}:
                return True
            if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
                return False
            return False
        return bool(v)

    @model_validator(mode="after")
    def _validate_runtime_bootstrap_config(self) -> "Settings":
        relay_fields = (
            self.relay_backend_url,
            self.relay_camera_id,
            self.relay_key_id,
            self.relay_private_key_pem,
        )
        if any(relay_fields) and not all(relay_fields):
            msg = (
                "Relay bootstrap config must set RELAY_BACKEND_URL, RELAY_CAMERA_ID, "
                "RELAY_KEY_ID, and RELAY_PRIVATE_KEY_PEM together or leave them all unset."
            )
            raise ValueError(msg)
        if self.relay_backend_url and self.relay_auth_scheme != _RELAY_AUTH_SCHEME_DEVICE_ASSERTION:
            msg = "RELAY_AUTH_SCHEME must be device_assertion when relay bootstrap credentials are configured."
            raise ValueError(msg)
        if self.image_sink == _IMAGE_SINK_BACKEND and not self.pairing_backend_url:
            msg = "IMAGE_SINK=backend requires PAIRING_BACKEND_URL."
            raise ValueError(msg)
        if self.image_sink == _IMAGE_SINK_S3:
            missing = [
                name
                for name, value in (
                    ("S3_ENDPOINT_URL", self.s3_endpoint_url),
                    ("S3_BUCKET", self.s3_bucket),
                    ("S3_ACCESS_KEY_ID", self.s3_access_key_id),
                    ("S3_SECRET_ACCESS_KEY", self.s3_secret_access_key),
                )
                if not value
            ]
            if missing:
                msg = f"IMAGE_SINK=s3 requires {', '.join(missing)}."
                raise ValueError(msg)
        return self


# Create a settings instance that can be imported throughout the app
settings: Settings = Settings()


def _is_running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def _uses_loopback_host(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.hostname in {"127.0.0.1", "localhost"}


def _set_authorized_api_keys(runtime_state: RuntimeState, keys: Iterable[str]) -> None:
    """Replace authorized API keys on runtime state."""
    runtime_state.replace_authorized_api_keys(set(dict.fromkeys(keys)))


def _add_authorized_api_key(runtime_state: RuntimeState, key: str) -> None:
    """Atomically add an authorized API key to runtime state."""
    if key in runtime_state.authorized_api_keys:
        return
    _set_authorized_api_keys(runtime_state, {*runtime_state.authorized_api_keys, key})


def apply_relay_credentials(runtime_state: RuntimeState) -> None:
    """Load relay credentials from persisted bootstrap state when env config is absent.

    Should be called once during application startup (lifespan), not at import time.
    """
    logger.info("Relay credentials path resolved to %s", _CREDENTIALS_FILE)
    if runtime_state.relay_enabled:
        logger.info(
            "Relay bootstrap credentials already present from static config; skipping persisted relay credentials"
        )
        return
    creds = load_relay_credentials()
    if creds:
        set_runtime_relay_credentials(
            runtime_state=runtime_state,
            relay_backend_url=str(creds.get("relay_backend_url", "")),
            relay_camera_id=str(creds.get("relay_camera_id", "")),
            relay_auth_scheme=str(creds.get("relay_auth_scheme", "device_assertion")),
            relay_key_id=str(creds.get("relay_key_id", "")),
            relay_private_key_pem=str(creds.get("relay_private_key_pem", "")),
        )
        if runtime_state.relay_backend_url.startswith("ws://"):
            logger.warning("Relay runtime is using unencrypted ws:// transport. Switch to wss:// in production.")


def resolve_image_sink_choice(app_settings: Settings = settings) -> str:
    """Resolve the effective image sink choice without instantiating it."""
    if app_settings.image_sink != _IMAGE_SINK_AUTO:
        return app_settings.image_sink
    if app_settings.s3_endpoint_url:
        return _IMAGE_SINK_S3
    if app_settings.pairing_backend_url:
        return _IMAGE_SINK_BACKEND
    return "unconfigured"


def clear_runtime_relay_credentials(runtime_state: RuntimeState) -> None:
    """Zero out all relay credential fields in runtime state."""
    runtime_state.clear_relay_credentials()


def _persist_local_api_key(key: str) -> None:
    """Add/update local_api_key in the shared credentials JSON file.

    Reads any existing content (relay creds, etc.) and merges the key in so
    nothing else is overwritten. Writes atomically via a temp file in the same
    directory, ``fchmod``s it to ``0o600`` before the rename, and ``fsync``s
    both the file and the containing directory so the final path is never
    briefly world-readable and is durable across a crash.
    """
    _CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, object] = {}
    if _CREDENTIALS_FILE.exists():
        with suppress(json.JSONDecodeError, OSError):
            existing = json.loads(_CREDENTIALS_FILE.read_text())
    existing["local_api_key"] = key
    payload = json.dumps(existing, indent=2)

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=_CREDENTIALS_FILE.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(payload)
            tmp.flush()
            # Tighten permissions BEFORE the rename so the final path is
            # never observable as world-readable, even if we crash right after.
            os.fchmod(tmp.fileno(), 0o600)
            os.fsync(tmp.fileno())
        tmp_path.replace(_CREDENTIALS_FILE)
        tmp_path = None  # ownership transferred to the final path
    except OSError:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink()
        raise
    logger.info("local_api_key persisted to %s", _CREDENTIALS_FILE)


def apply_local_mode(runtime_state: RuntimeState, app_settings: Settings = settings) -> None:
    """Load or generate the local API key on startup.

    Always runs regardless of ``local_mode_enabled`` so the key exists for
    relay delivery via ``/local-access-info``. The key is only injected into
    ``authorized_api_keys`` when ``local_mode_enabled`` is True, so setting
    ``LOCAL_MODE_ENABLED=false`` disables direct-connection authentication
    while keeping the key available for the relay bootstrap endpoint.

    Should be called once during application startup (lifespan) after
    ``apply_relay_credentials()``, so credentials are fully loaded first.
    """
    # Priority: env-provided > credentials file > auto-generate
    local_api_key = runtime_state.local_api_key or app_settings.local_api_key
    if not local_api_key:
        creds = load_relay_credentials()
        local_api_key = str(creds.get("local_api_key", "")) if creds else ""

    if not local_api_key:
        local_api_key = f"local_{secrets.token_urlsafe(32)}"
        _persist_local_api_key(local_api_key)
        logger.info("Local mode: generated new API key and persisted to credentials file")

    runtime_state.set_local_api_key(local_api_key)
    if app_settings.local_mode_enabled:
        _add_authorized_api_key(runtime_state, local_api_key)
        logger.info("Local mode active — direct connection API key loaded")
    else:
        logger.info("Local API key loaded (local_mode_enabled=False — direct auth disabled)")


def set_runtime_relay_credentials(
    runtime_state: RuntimeState,
    *,
    relay_backend_url: str,
    relay_camera_id: str,
    relay_auth_scheme: str,
    relay_key_id: str,
    relay_private_key_pem: str,
) -> None:
    """Apply relay credentials at runtime and refresh dependent auth state."""
    runtime_state.set_relay_credentials(
        relay_backend_url=relay_backend_url,
        relay_camera_id=relay_camera_id,
        relay_auth_scheme=relay_auth_scheme,
        relay_key_id=relay_key_id,
        relay_private_key_pem=relay_private_key_pem,
    )
    _add_authorized_api_key(runtime_state, runtime_state.local_relay_api_key)


def bootstrap_runtime_state(runtime_state: RuntimeState, app_settings: Settings = settings) -> None:
    """Apply runtime bootstrap precedence and emit startup-facing config logs."""
    apply_relay_credentials(runtime_state)
    apply_local_mode(runtime_state, app_settings)
    logger.info("Image sink resolved to %s", resolve_image_sink_choice(app_settings))
    if not app_settings.local_mode_enabled and runtime_state.local_api_key:
        logger.warning(
            "LOCAL_MODE_ENABLED=false but a local API key exists; the key remains available for relay bootstrap only."
        )
    if (
        app_settings.pairing_backend_url
        and _uses_loopback_host(app_settings.pairing_backend_url)
        and _is_running_in_container()
    ):
        logger.warning(
            "PAIRING_BACKEND_URL uses loopback inside a container; pairing will rewrite it to host.docker.internal."
        )
