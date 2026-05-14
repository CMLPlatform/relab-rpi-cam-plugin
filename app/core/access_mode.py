"""Derived connection mode helper."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.runtime_state import RuntimeState
    from app.core.settings import Settings


class ConnectionMode(StrEnum):
    """High-level relay/pairing state derived from runtime credentials."""

    PAIRED = "paired"
    PAIRING = "pairing"
    IDLE = "idle"


def connection_mode(runtime_state: RuntimeState, app_settings: Settings) -> ConnectionMode:
    """Derive the active connection mode from runtime credentials and pairing config."""
    if runtime_state.relay_enabled:
        return ConnectionMode.PAIRED
    if app_settings.pairing_backend_url:
        return ConnectionMode.PAIRING
    return ConnectionMode.IDLE
