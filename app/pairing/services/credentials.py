"""On-disk relay credentials file I/O.

Extracted from ``service.py`` so ``app.core.bootstrap`` can import the
credentials loader without pulling in the pairing orchestration module
(which in turn needs bootstrap helpers) — breaking the previous
startup-time import cycle.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import suppress
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_credentials_file() -> Path:
    """Return the path to the relay credentials file.

    Respects ``RELAB_CREDENTIALS_FILE`` if set; otherwise falls back to
    ``~/.config/relab/relay_credentials.json`` (XDG-style).
    """
    if env_path := os.getenv("RELAB_CREDENTIALS_FILE"):
        return Path(env_path)
    return Path.home() / ".config" / "relab" / "relay_credentials.json"


_CREDENTIALS_FILE = _get_credentials_file()


def save_relay_credentials(
    relay_backend_url: str,
    camera_id: str,
    relay_auth_scheme: str,
    key_id: str,
    private_key_pem: str,
) -> None:
    """Persist relay credentials atomically to the JSON credentials file.

    Writes via a ``0600`` temp file + ``Path.replace`` so credentials are not
    briefly world-readable and a power loss cannot leave a truncated file.
    """
    data = {
        "relay_backend_url": relay_backend_url,
        "relay_camera_id": camera_id,
        "relay_auth_scheme": relay_auth_scheme,
        "relay_key_id": key_id,
        "relay_private_key_pem": private_key_pem,
    }
    _CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=_CREDENTIALS_FILE.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(json.dumps(data, indent=2))
            tmp.flush()
            os.fchmod(tmp.fileno(), 0o600)
            os.fsync(tmp.fileno())
        tmp_path.replace(_CREDENTIALS_FILE)
    except OSError:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink()
        raise
    logger.info("Relay credentials saved to %s", _CREDENTIALS_FILE)


def load_relay_credentials() -> dict[str, str | bool] | None:
    """Load relay credentials from the JSON file, if it exists."""
    if not _CREDENTIALS_FILE.exists():
        return None
    try:
        return json.loads(_CREDENTIALS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to read %s", _CREDENTIALS_FILE)
        return None


def delete_relay_credentials() -> None:
    """Delete the on-disk credentials file, if present."""
    try:
        _CREDENTIALS_FILE.unlink(missing_ok=True)
        logger.info("Relay credentials deleted from %s", _CREDENTIALS_FILE)
    except OSError as exc:
        logger.warning("Failed to delete relay credentials file: %s", exc)
