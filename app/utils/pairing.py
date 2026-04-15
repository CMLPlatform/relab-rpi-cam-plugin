"""Pairing mode: auto-register with the ReLab backend without manual credential entry.

When the RPi boots without relay credentials but has a `pairing_backend_url` configured,
it enters pairing mode:
1. Generates a 6-char code and registers it with the backend.
2. Displays the code on its setup page for the user to enter in the ReLab app.
3. Polls the backend until the user claims the code.
4. Receives credentials, saves them to a separate JSON file, and starts the relay.
"""

from __future__ import annotations

import asyncio
import base64
import json as json_mod
import logging
import os
import secrets
import socket
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import app.core.config as core_config
from app.core.runtime_context import get_active_runtime
from app.utils.logging import build_log_extra
from app.utils.pairing_client import PairingClient
from relab_rpi_cam_models import PairingClaimedBootstrap, PairingStatus, RelayAuthScheme

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

logger = logging.getLogger(__name__)


def _get_credentials_file() -> Path:
    """Get the path to the relay credentials file.

    Respects env var RELAB_CREDENTIALS_FILE if set, otherwise uses:
    ~/.config/relab/relay_credentials.json (following XDG Base Directory Spec)
    """
    if env_path := os.getenv("RELAB_CREDENTIALS_FILE"):
        return Path(env_path)
    config_dir = Path.home() / ".config" / "relab"
    return config_dir / "relay_credentials.json"


_CREDENTIALS_FILE = _get_credentials_file()
_CODE_LENGTH = 3  # token_hex(3) → 6 hex chars
PAIRING_CODE_TTL_SECONDS = 10 * 60

# Pairing status values
STATUS_WAITING = "waiting"
STATUS_PAIRED = "paired"
AUTH_SCHEME_DEVICE_ASSERTION = "device_assertion"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}
_DOCKER_HOST_ALIAS = "host.docker.internal"


class PairingCodeExpiredError(RuntimeError):
    """Raised when the active pairing code expires and should be rotated."""


class PairingBackendNotFoundError(RuntimeError):
    """Raised when the configured pairing backend does not expose the pairing API."""


@dataclass
class PairingState:
    """Observable pairing state for the setup page."""

    code: str | None = None
    fingerprint: str | None = None
    expires_at: datetime | None = None
    status: str = "idle"  # idle | registering | waiting | paired | error
    error: str | None = None


class PairingService:
    """Runtime-owned pairing orchestration and observable state."""

    def __init__(self) -> None:
        self.state = PairingState()

    def reset_state(self) -> None:
        """Clear the active pairing code and return to idle."""
        _clear_transient_pairing_state(self.state, status="idle")

    def get_state(self) -> PairingState:
        """Return the current pairing state."""
        return self.state

    def log_mode_started(self) -> None:
        """Emit a headless-friendly startup message for pairing mode."""
        logger.info(
            "PAIRING MODE | state=awaiting_claim setup=%s pairing_backend=%s",
            _pairing_setup_location(),
            core_config.settings.pairing_backend_url.rstrip("/"),
            extra=build_log_extra(),
        )

    async def run_forever(self, on_paired: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Run the pairing flow until credentials are obtained or a fatal error occurs."""
        base = _normalize_pairing_backend_base_url(core_config.settings.pairing_backend_url.rstrip("/"))
        if not base:
            return

        async with httpx.AsyncClient(timeout=10) as client:
            while True:
                try:
                    await self._pairing_cycle(client, base, on_paired)
                except PairingCodeExpiredError:
                    continue
                except PairingBackendNotFoundError:
                    logger.exception("Pairing backend missing pairing API | stopping pairing")
                    _clear_transient_pairing_state(
                        self.state,
                        status="error",
                        error="Pairing backend is reachable, but the pairing API was not found at the configured URL.",
                    )
                    return
                except httpx.HTTPStatusError as exc:
                    _log_pairing_http_status_error(exc)
                    logger.exception("Pairing cycle failed | retry_in_s=10")
                    _clear_transient_pairing_state(self.state, status="error", error="Pairing failed — retrying…")
                    await asyncio.sleep(10)
                except httpx.ConnectError as exc:
                    _log_pairing_connect_error(exc, base)
                    logger.exception("Pairing cycle failed | retry_in_s=10")
                    _clear_transient_pairing_state(
                        self.state, status="error", error="Pairing backend unreachable — retrying…"
                    )
                    await asyncio.sleep(10)
                except Exception:
                    logger.exception("Pairing cycle failed | retry_in_s=10")
                    _clear_transient_pairing_state(self.state, status="error", error="Pairing failed — retrying…")
                    await asyncio.sleep(10)
                else:
                    return

    def _set_pairing_code_state(self, code: str, fingerprint: str) -> None:
        _set_pairing_code_state(self.state, code, fingerprint)

    def _prepare_registration_state(self, registration: PairingRegistration) -> None:
        self._set_pairing_code_state(registration.code, registration.fingerprint)
        self.state.status = "registering"
        self.state.error = None
        _log_pairing_ready(registration.code)

    async def _pairing_cycle(
        self,
        client: PairingClient | httpx.AsyncClient,
        base_url: str,
        on_paired: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        pairing_client = _coerce_pairing_client(client, base_url)
        registration = await self._register_pairing_code(pairing_client)
        self.state.status = STATUS_WAITING
        poll_result = await _poll_pairing_status(pairing_client, registration.code, registration.fingerprint)
        await self._complete_pairing(poll_result, registration.private_key, on_paired)

    async def _register_pairing_code(self, client: PairingClient) -> PairingRegistration:
        return await _register_pairing_code_with_client(client, self.state)

    def _clear_active_pairing_code(self) -> None:
        _clear_active_pairing_code(self.state)

    async def _complete_pairing(
        self,
        payload: PairingClaimedBootstrap,
        private_key: ec.EllipticCurvePrivateKey,
        on_paired: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        await _complete_pairing_state(self.state, payload, private_key, on_paired)


@dataclass(frozen=True)
class PairingRegistration:
    """Material needed for one pairing registration attempt."""

    code: str
    fingerprint: str
    private_key: ec.EllipticCurvePrivateKey
    key_id: str
    public_key_jwk: dict[str, str]


def _clear_transient_pairing_state(state: PairingState, *, status: str, error: str | None = None) -> None:
    """Reset transient pairing state after a failed cycle or before restart."""
    state.code = None
    state.fingerprint = None
    state.expires_at = None
    state.status = status
    state.error = error


def _sanitize_log_value(value: object) -> str:
    """Normalize a value before logging it."""
    return str(value).replace("\r", " ").replace("\n", " ")


def _coerce_pairing_client(client: PairingClient | httpx.AsyncClient, base_url: str) -> PairingClient:
    """Normalize a raw httpx client or an existing PairingClient to PairingClient."""
    if isinstance(client, PairingClient):
        return client
    return PairingClient(client, base_url)


def _pairing_code_expires_at() -> datetime:
    """Return the expiry timestamp for the currently active pairing code."""
    return datetime.now(UTC) + timedelta(seconds=PAIRING_CODE_TTL_SECONDS)


def _set_pairing_code_state(state: PairingState, code: str, fingerprint: str) -> None:
    """Store the active pairing code and its expiry on the observable state."""
    state.code = code
    state.fingerprint = fingerprint
    state.expires_at = _pairing_code_expires_at()


def _pairing_setup_location() -> str:
    """Return the best operator-facing setup location for pairing."""
    base_url = str(core_config.settings.base_url).rstrip("/")
    if base_url:
        parsed = urlparse(base_url)
        if parsed.hostname not in _LOOPBACK_HOSTS:
            return f"{base_url}/setup"
        if lan_url := _lan_setup_url(parsed.port):
            return lan_url
    return "/setup"


def _is_running_in_container() -> bool:
    """Best-effort Docker/container detection for local-dev URL handling."""
    return Path("/.dockerenv").exists()


def _normalize_pairing_backend_base_url(base_url: str) -> str:
    """Rewrite loopback backends to the Docker host alias when needed.

    Inside a container, http://localhost points back at the container itself.
    For local development where the RELab backend runs on the host machine,
    transparently switch to host.docker.internal so the plugin can reach it.
    """
    parsed = urlparse(base_url)
    if parsed.hostname not in _LOOPBACK_HOSTS or not _is_running_in_container():
        return base_url

    rewritten = parsed._replace(netloc=parsed.netloc.replace(parsed.hostname, _DOCKER_HOST_ALIAS, 1))
    normalized = urlunparse(rewritten)
    logger.warning(
        "PAIRING BACKEND URL uses loopback inside a container; using %s instead of %s",
        normalized,
        base_url,
        extra=build_log_extra(),
    )
    return normalized


def _lan_setup_url(port: int | None) -> str | None:
    """Best-effort LAN setup URL when the configured base URL is loopback-only."""
    setup_port = port or 8018
    with suppress(OSError):
        hostname = socket.gethostname()
        _, _, addresses = socket.gethostbyname_ex(hostname)
        for address in addresses:
            if address and address not in _LOOPBACK_HOSTS and not address.startswith("127."):
                return f"http://{address}:{setup_port}/setup"
    return None


def _format_pairing_ready_message(code: str) -> str:
    """Return a bannered pairing message that stays readable in Docker logs."""
    sep = "═" * 54
    return (
        f"\n{sep}\n"
        f"  PAIRING READY\n"
        f"  PAIRING CODE: {_sanitize_log_value(code)}\n"
        f"  Setup    : {_pairing_setup_location()}\n"
        f"  Backend  : {core_config.settings.pairing_backend_url.rstrip('/')}\n"
        f"  Claim in : RELab app > Cameras > Add Camera\n"
        f"{sep}"
    )


def _log_pairing_ready(code: str) -> None:
    """Emit the currently active pairing code for operators over SSH/logs."""
    logger.info("%s", _format_pairing_ready_message(code), extra=build_log_extra())


def _log_pairing_connect_error(exc: httpx.ConnectError, base_url: str) -> None:
    """Log actionable guidance for unreachable pairing backends."""
    del exc
    parsed = urlparse(base_url)
    if parsed.hostname in _LOOPBACK_HOSTS and _is_running_in_container():
        logger.error(
            "Pairing backend %s is loopback from inside the container. "
            "Use the host machine via http://%s:%s, a LAN IP, or the real HTTPS backend.",
            base_url,
            _DOCKER_HOST_ALIAS,
            parsed.port or 80,
            extra=build_log_extra(),
        )
        return

    logger.error("Pairing backend %s could not be reached.", base_url, extra=build_log_extra())


def _log_pairing_http_status_error(exc: httpx.HTTPStatusError) -> None:
    """Log actionable guidance for backend rejections during pairing."""
    response = exc.response
    request = exc.request
    body_snippet = response.text.strip().replace("\n", " ")
    if len(body_snippet) > 160:
        body_snippet = f"{body_snippet[:157]}..."

    if response.status_code == 403 and request.url.path.endswith("/pairing/register"):
        logger.error(
            "Pairing registration was rejected by %s (HTTP 403). "
            "The backend is reachable, but this environment is refusing anonymous camera registration. "
            "Response body: %s",
            request.url,
            body_snippet or "<empty>",
        )
        return

    logger.error(
        "Pairing request to %s failed with HTTP %s. Response body: %s",
        request.url,
        response.status_code,
        body_snippet or "<empty>",
    )


def _log_pairing_timeout(stage: str, code: str, retry_in_s: int) -> None:
    """Log a transient timeout during pairing with consistent wording."""
    logger.warning("PAIRING %s TIMEOUT | code=%s retry_in_s=%s", stage, code, retry_in_s)


def _new_pairing_registration() -> PairingRegistration:
    code, fingerprint = _generate_code_and_fingerprint()
    private_key = _generate_private_key()
    key_id = secrets.token_urlsafe(16)
    return PairingRegistration(
        code=code,
        fingerprint=fingerprint,
        private_key=private_key,
        key_id=key_id,
        public_key_jwk=_public_jwk(private_key, key_id),
    )


def _prepare_registration_state(state: PairingState, registration: PairingRegistration) -> None:
    _set_pairing_code_state(state, registration.code, registration.fingerprint)
    state.status = "registering"
    state.error = None
    _log_pairing_ready(registration.code)


async def _register_pairing_code_with_client(
    client: PairingClient,
    state: PairingState,
) -> PairingRegistration:
    registration = _new_pairing_registration()
    _prepare_registration_state(state, registration)

    for _attempt in range(3):
        try:
            resp = await client.register(
                code=registration.code,
                fingerprint=registration.fingerprint,
                public_key_jwk=registration.public_key_jwk,
                key_id=registration.key_id,
            )
        except httpx.TimeoutException:
            retry_delay_s = core_config.settings.pairing_register_timeout_retry_s
            _log_pairing_timeout("REGISTER", registration.code, retry_delay_s)
            await asyncio.sleep(retry_delay_s)
            continue

        if resp.status_code == 201:
            return registration
        if resp.status_code == 409:
            registration = _new_pairing_registration()
            _prepare_registration_state(state, registration)
            continue
        if resp.status_code == 404:
            msg = "Pairing register endpoint returned 404. Check PAIRING_BACKEND_URL and backend deployment."
            raise PairingBackendNotFoundError(msg)

        resp.raise_for_status()

    msg = "Failed to register pairing code after 3 attempts."
    raise RuntimeError(msg)


async def _register_pairing_code(
    client: httpx.AsyncClient,
    base_url: str,
    state: PairingState,
) -> PairingRegistration:
    return await _register_pairing_code_with_client(PairingClient(client, base_url), state)


async def _poll_pairing_status(
    client: PairingClient,
    code: str,
    fingerprint: str,
) -> PairingClaimedBootstrap:
    while True:
        poll_interval_s = core_config.settings.pairing_poll_interval_s
        await asyncio.sleep(poll_interval_s)
        try:
            resp = await client.poll(code=code, fingerprint=fingerprint)
        except httpx.TimeoutException:
            _log_pairing_timeout("POLL", code, poll_interval_s)
            continue

        if resp.status_code == 404:
            logger.warning("PAIRING ROTATING | expired_code=%s reason=expired", code)
            raise PairingCodeExpiredError

        resp.raise_for_status()
        data = client.parse_poll_response(resp.json())
        if data.status == PairingStatus.WAITING:
            continue
        return PairingClaimedBootstrap(
            camera_id=str(data.camera_id),
            ws_url=str(data.ws_url),
            auth_scheme=RelayAuthScheme(str(data.auth_scheme)),
            key_id=str(data.key_id),
        )


def _clear_active_pairing_code(state: PairingState) -> None:
    state.code = None
    state.fingerprint = None
    state.expires_at = None


async def _complete_pairing_state(
    state: PairingState,
    payload: PairingClaimedBootstrap,
    private_key: ec.EllipticCurvePrivateKey,
    on_paired: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    camera_id = payload.camera_id
    relay_backend_url = payload.ws_url
    relay_auth_scheme = payload.auth_scheme.value
    key_id = payload.key_id
    private_key_pem = _private_key_pem(private_key)

    logger.info("PAIRING COMPLETE | camera_id=%s relay_starting=true", camera_id)
    state.status = STATUS_PAIRED
    _clear_active_pairing_code(state)

    _save_relay_credentials(
        relay_backend_url=relay_backend_url,
        camera_id=camera_id,
        relay_auth_scheme=relay_auth_scheme,
        key_id=key_id,
        private_key_pem=private_key_pem,
    )
    core_config.set_runtime_relay_credentials(
        get_active_runtime().runtime_state,
        relay_backend_url=relay_backend_url,
        relay_camera_id=camera_id,
        relay_auth_scheme=relay_auth_scheme,
        relay_key_id=key_id,
        relay_private_key_pem=private_key_pem,
    )
    await on_paired()


async def _complete_pairing(
    state: PairingState,
    payload: PairingClaimedBootstrap | dict[str, object],
    private_key: ec.EllipticCurvePrivateKey,
    on_paired: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    if not isinstance(payload, PairingClaimedBootstrap):
        payload = PairingClaimedBootstrap.model_validate(payload)
    await _complete_pairing_state(state, payload, private_key, on_paired)


def _generate_code_and_fingerprint() -> tuple[str, str]:
    code = secrets.token_hex(_CODE_LENGTH).upper()
    fingerprint = secrets.token_urlsafe(16)
    return code, fingerprint


def _generate_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _public_jwk(private_key: ec.EllipticCurvePrivateKey, key_id: str) -> dict[str, str]:
    public_numbers = private_key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64url_uint(public_numbers.x),
        "y": _b64url_uint(public_numbers.y),
        "kid": key_id,
    }


def _private_key_pem(private_key: ec.EllipticCurvePrivateKey) -> str:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _save_relay_credentials(
    relay_backend_url: str,
    camera_id: str,
    relay_auth_scheme: str,
    key_id: str,
    private_key_pem: str,
) -> None:
    """Persist relay credentials to a separate JSON file (not .env).

    This avoids corrupting the user's .env which may contain comments,
    quotes, or other settings. The config loads these on next boot.
    Writes atomically to prevent corruption on power loss.
    Ensures the credentials directory exists before writing.
    """
    data = {
        "relay_backend_url": relay_backend_url,
        "relay_camera_id": camera_id,
        "relay_auth_scheme": relay_auth_scheme,
        "relay_key_id": key_id,
        "relay_private_key_pem": private_key_pem,
    }
    # Ensure the directory exists
    _CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file first, then atomically replace
    with tempfile.NamedTemporaryFile(
        mode="w", dir=_CREDENTIALS_FILE.parent, delete=False, suffix=".tmp", encoding="utf-8"
    ) as tmp:
        tmp_path = tmp.name
        tmp.write(json_mod.dumps(data, indent=2))
    try:
        Path(tmp_path).replace(_CREDENTIALS_FILE)
        _CREDENTIALS_FILE.chmod(0o600)
    except OSError:
        # Clean up temp file if replace fails
        with suppress(OSError):
            Path(tmp_path).unlink()
        raise
    logger.info("Relay credentials saved to %s", _CREDENTIALS_FILE)


def load_relay_credentials() -> dict[str, str | bool] | None:
    """Load relay credentials from the JSON file, if it exists."""
    if not _CREDENTIALS_FILE.exists():
        return None
    try:
        return json_mod.loads(_CREDENTIALS_FILE.read_text())
    except (json_mod.JSONDecodeError, OSError):
        logger.warning("Failed to read %s", _CREDENTIALS_FILE)
        return None


def delete_relay_credentials() -> None:
    """Delete the on-disk credentials file.

    Does not touch runtime settings (relay_backend_url etc.) — call
    ``clear_runtime_relay_credentials()`` separately for that.
    """
    try:
        _CREDENTIALS_FILE.unlink(missing_ok=True)
        logger.info("Relay credentials deleted from %s", _CREDENTIALS_FILE)
    except OSError as exc:
        logger.warning("Failed to delete relay credentials file: %s", exc)
