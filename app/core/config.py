"""Configuration settings for the Raspberry Pi API app."""

import warnings
from pathlib import Path

from pydantic import HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Set the project base directory and .env file
BASE_DIR: Path = (Path(__file__).resolve().parents[2]).resolve()


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
    hls_path: Path = BASE_DIR / "data" / "hls"  # Used for storing temporary HLS video files
    image_path: Path = BASE_DIR / "data" / "images"  # Used for temporary storing of captured images
    templates_path: Path = BASE_DIR / "app" / "templates"  # Used for storing HTML templates
    static_path: Path = BASE_DIR / "app" / "static"  # Used for serving static files
    log_path: Path = BASE_DIR / "logs"  # Used for storing log files

    # HLS settings
    hls_manifest_filename: str = "master.m3u8"

    # Directory cleanup settings
    cleanup_interval_s: int = 60 * 10  # Interval for cleaning up expired files in seconds (10 minutes)
    hls_ttl_s: int = 60  # Time-to-live for HLS video files in seconds (1 minute)
    image_ttl_s: int = 60 * 60  # Time-to-live for captured images in seconds (1 hour)
    max_stream_duration_s: int = 60 * 60 * 5  # Maximum duration for a stream in seconds (5 hours)
    check_stream_interval_s: int = 60  # Interval for checking stream duration in seconds (1 minute)

    # Camera settings
    camera_standby_s: int = 60 * 10  # Camera standby time in seconds (10 minutes)

    # Auth
    auth_key_name: str = "X-API-Key"

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


# Create a settings instance that can be imported throughout the app
settings: Settings = Settings()


def apply_relay_credentials() -> None:
    """Load relay credentials from pairing JSON file (written by pairing flow).

    Should be called once during application startup (lifespan), not at import time.
    """
    from app.utils.pairing import load_relay_credentials  # noqa: PLC0415

    creds = load_relay_credentials()
    if creds:
        settings.relay_backend_url = str(creds.get("relay_backend_url", ""))
        settings.relay_camera_id = str(creds.get("relay_camera_id", ""))
        settings.relay_api_key = str(creds.get("relay_api_key", ""))

    # Ensure the relay API key is accepted by the local API for loopback calls.
    if settings.relay_api_key and settings.relay_api_key not in settings.authorized_api_keys:
        settings.authorized_api_keys.append(settings.relay_api_key)
