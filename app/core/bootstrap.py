"""Runtime bootstrap: apply persisted credentials, generate local keys, emit startup logs."""

import json
import logging
import os
import secrets
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse

from app.api.services.pairing import _CREDENTIALS_FILE, load_relay_credentials
from app.core.runtime_state import RuntimeState
from app.core.settings import IMAGE_SINK_AUTO, IMAGE_SINK_BACKEND, IMAGE_SINK_S3, Settings, settings

logger = logging.getLogger(__name__)


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
    if app_settings.image_sink != IMAGE_SINK_AUTO:
        return app_settings.image_sink
    if app_settings.s3_endpoint_url:
        return IMAGE_SINK_S3
    if app_settings.pairing_backend_url:
        return IMAGE_SINK_BACKEND
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
    relay delivery via ``/system/local-access``. The key is only injected into
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
