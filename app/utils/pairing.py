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
import json as json_mod
import logging
import os
import secrets
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

import app.core.config as core_config

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
_POLL_INTERVAL_S = 3
_CODE_LENGTH = 3  # token_hex(3) → 6 hex chars

# Pairing status values
STATUS_WAITING = "waiting"
STATUS_PAIRED = "paired"


@dataclass
class PairingState:
    """Observable pairing state for the setup page."""

    code: str | None = None
    fingerprint: str | None = None
    status: str = "idle"  # idle | registering | waiting | paired | error
    error: str | None = None


_state = PairingState()


def get_pairing_state() -> PairingState:
    """Return the current pairing state (read by the setup page)."""
    return _state


async def run_pairing(on_paired: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Run the pairing flow: register → poll → configure → callback."""
    base = core_config.settings.pairing_backend_url.rstrip("/")
    if not base:
        return

    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                await _pairing_cycle(client, base, on_paired)
            except Exception:
                logger.exception("Pairing cycle failed, retrying in 10s")
                _state.status = "error"
                _state.error = "Pairing failed — retrying…"
                await asyncio.sleep(10)
            else:
                return  # Successfully paired


async def _pairing_cycle(
    client: httpx.AsyncClient,
    base_url: str,
    on_paired: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    """Single pairing attempt: register a code and poll until claimed."""
    code, fingerprint = _generate_code_and_fingerprint()
    _state.code = code
    _state.fingerprint = fingerprint
    _state.status = "registering"
    _state.error = None

    # Register
    for _attempt in range(3):
        resp = await client.post(
            f"{base_url}/plugins/rpi-cam/pairing/register",
            json={"code": code, "rpi_fingerprint": fingerprint},
        )
        if resp.status_code == 201:
            break
        if resp.status_code == 409:
            # Code collision — regenerate
            code, fingerprint = _generate_code_and_fingerprint()
            _state.code = code
            _state.fingerprint = fingerprint
            continue
        resp.raise_for_status()
    else:
        msg = "Failed to register pairing code after 3 attempts."
        raise RuntimeError(msg)

    logger.info("Pairing code registered: %s", code)
    _state.status = "waiting"

    # Poll
    while True:
        await asyncio.sleep(_POLL_INTERVAL_S)
        resp = await client.get(
            f"{base_url}/plugins/rpi-cam/pairing/poll",
            params={"code": code, "fingerprint": fingerprint},
        )
        if resp.status_code == 404:
            # Code expired — restart cycle
            logger.warning("Pairing code %s expired, regenerating.", code)
            msg = "Pairing code expired"
            raise RuntimeError(msg)

        resp.raise_for_status()
        data = resp.json()

        if data["status"] == STATUS_WAITING:
            continue

        if data["status"] == STATUS_PAIRED:
            logger.info("Pairing complete! Camera ID: %s", data["camera_id"])
            _state.status = STATUS_PAIRED

            # Persist credentials to a separate JSON file (not .env)
            _save_relay_credentials(
                relay_backend_url=data["ws_url"],
                camera_id=data["camera_id"],
                api_key=data["api_key"],
            )

            # Update in-memory settings
            core_config.settings.relay_backend_url = data["ws_url"]
            core_config.settings.relay_camera_id = data["camera_id"]
            core_config.settings.relay_api_key = data["api_key"]

            # Start the relay
            await on_paired()
            return


def _generate_code_and_fingerprint() -> tuple[str, str]:
    code = secrets.token_hex(_CODE_LENGTH).upper()
    fingerprint = secrets.token_urlsafe(16)
    return code, fingerprint


def _save_relay_credentials(
    relay_backend_url: str,
    camera_id: str,
    api_key: str,
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
        "relay_api_key": api_key,
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
