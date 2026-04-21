"""Back-compat re-exports.

The settings class moved to ``app.core.settings`` and the bootstrap helpers to
``app.core.bootstrap``. This module stays as a shim so existing imports keep
working; new code should import from the focused modules directly.
"""

from app.core.bootstrap import (
    apply_local_mode,
    apply_relay_credentials,
    bootstrap_runtime_state,
    clear_runtime_relay_credentials,
    resolve_image_sink_choice,
    set_runtime_relay_credentials,
)
from app.core.settings import BASE_DIR, DEFAULT_PAIRING_BACKEND_URL, Settings, settings

__all__ = [
    "BASE_DIR",
    "DEFAULT_PAIRING_BACKEND_URL",
    "Settings",
    "apply_local_mode",
    "apply_relay_credentials",
    "bootstrap_runtime_state",
    "clear_runtime_relay_credentials",
    "resolve_image_sink_choice",
    "set_runtime_relay_credentials",
    "settings",
]
