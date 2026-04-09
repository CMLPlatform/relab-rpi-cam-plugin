"""Configuration settings for the Raspberry Pi API app."""

import warnings
from pathlib import Path
from collections.abc import Iterable
from typing import Literal, cast

from pydantic import HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.pairing import load_relay_credentials

# Set the project base directory and .env file
BASE_DIR: Path = (Path(__file__).resolve().parents[2]).resolve()
_HTTPS_SCHEME = "https"


class Settings(BaseSettings):
    """Settings class to store all the configurations for the app."""

    base_url: HttpUrl = HttpUrl("http://127.0.0.1:8018/")  # Base URL for the Raspberry Pi server
    allowed_cors_origins: list[HttpUrl] = [  # Allowed origins for CORS. Must include the main API host
        HttpUrl("http://127.0.0.1:8000"),
        HttpUrl("http://localhost:8000"),
        HttpUrl("https://cml-relab.org"),
    ]
    authorized_api_keys: list[str] = []  # API keys from users of the main API
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
    camera_standby_s: int = 60 * 10  # Camera standby time in seconds (10 minutes)

    # Auth
    auth_key_name: str = "X-API-Key"
    auth_cookie_secure: bool | None = None
    session_cookie_name: str = "relab_session"

    # Debug mode
    debug: bool = False

    # WebSocket relay (auto-enabled when all three fields are set)
    relay_backend_url: str = ""  # wss://your-backend/plugins/rpi-cam/ws/connect
    relay_camera_id: str = ""
    relay_api_key: str = ""

    @property
    def relay_enabled(self) -> bool:
        """Relay is enabled when all three relay fields are set."""
        return bool(self.relay_backend_url and self.relay_camera_id and self.relay_api_key)

    @property
    def cookie_secure(self) -> bool:
        """Return whether auth cookies should be marked secure."""
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.base_url.scheme == _HTTPS_SCHEME

    # Pairing: set this to the backend's HTTP(S) API URL to enable zero-config pairing.
    # When set and relay credentials are absent, the RPi enters pairing mode on boot.
    pairing_backend_url: str = ""  # https://your-backend/api

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
            return cast(list[str], v)
        if not isinstance(v, str):
            if isinstance(v, Iterable):
                return cast(list[str], list(v))
            return []
        stripped = v.strip()
        if not stripped:
            return []
        import json  # noqa: PLC0415

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            # Fall back to comma-separated: "key1, key2" or "[key1, key2]"
            stripped = stripped.strip("[]")
            return [k.strip().strip("\"'") for k in stripped.split(",") if k.strip()]
        if not isinstance(parsed, list):
            return [parsed]
        return parsed

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


# Create a settings instance that can be imported throughout the app
settings: Settings = Settings()


def apply_relay_credentials() -> None:
    """Load relay credentials from pairing JSON file (written by pairing flow).

    Should be called once during application startup (lifespan), not at import time.
    """
    creds = load_relay_credentials()
    if creds:
        set_runtime_relay_credentials(
            relay_backend_url=str(creds.get("relay_backend_url", "")),
            relay_camera_id=str(creds.get("relay_camera_id", "")),
            relay_api_key=str(creds.get("relay_api_key", "")),
        )


def set_runtime_relay_credentials(
    *,
    relay_backend_url: str,
    relay_camera_id: str,
    relay_api_key: str,
) -> None:
    """Apply relay credentials at runtime and refresh dependent auth state."""
    settings.relay_backend_url = relay_backend_url
    settings.relay_camera_id = relay_camera_id
    settings.relay_api_key = relay_api_key

    # Ensure the relay API key is accepted by the local API for loopback calls.
    if settings.relay_api_key and settings.relay_api_key not in settings.authorized_api_keys:
        settings.authorized_api_keys.append(settings.relay_api_key)

    # Refresh the pre-computed auth key hashes after modifying the key list.
    from app.api.dependencies.auth import reload_authorized_hashes  # noqa: PLC0415 — deferred to avoid circular import

    reload_authorized_hashes()
