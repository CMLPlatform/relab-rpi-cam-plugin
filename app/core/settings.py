"""Pydantic settings for the Raspberry Pi API app."""

import json
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, cast

from pydantic import HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Set the project base directory and .env file
BASE_DIR: Path = (Path(__file__).resolve().parents[2]).resolve()
HTTPS_SCHEME = "https"
RELAY_AUTH_SCHEME_DEVICE_ASSERTION = "device_assertion"
IMAGE_SINK_AUTO = "auto"
IMAGE_SINK_BACKEND = "backend"
IMAGE_SINK_S3 = "s3"
DEFAULT_PAIRING_BACKEND_URL = "https://api.cml-relab.org"


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
    # /system/local-access endpoint — no manual copying required.
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
            and self.relay_auth_scheme == RELAY_AUTH_SCHEME_DEVICE_ASSERTION
            and self.relay_key_id
            and self.relay_private_key_pem
        )

    @property
    def cookie_secure(self) -> bool:
        """Return whether auth cookies should be marked secure."""
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.base_url.scheme == HTTPS_SCHEME

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
        if self.relay_backend_url and self.relay_auth_scheme != RELAY_AUTH_SCHEME_DEVICE_ASSERTION:
            msg = "RELAY_AUTH_SCHEME must be device_assertion when relay bootstrap credentials are configured."
            raise ValueError(msg)
        if self.image_sink == IMAGE_SINK_BACKEND and not self.pairing_backend_url:
            msg = "IMAGE_SINK=backend requires PAIRING_BACKEND_URL."
            raise ValueError(msg)
        if self.image_sink == IMAGE_SINK_S3:
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
