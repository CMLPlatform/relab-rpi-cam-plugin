"""Runtime-owned mutable state derived from static settings and persisted credentials."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings

_DEVICE_ASSERTION_AUTH_SCHEME = "device_assertion"


@dataclass
class RuntimeState:
    """Own live mutable process state that should not live on env settings."""

    relay_backend_url: str = ""
    relay_camera_id: str = ""
    relay_auth_scheme: str = _DEVICE_ASSERTION_AUTH_SCHEME
    relay_key_id: str = ""
    relay_private_key_pem: str = ""
    local_relay_api_key: str = ""
    local_api_key: str = ""
    authorized_api_keys: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_settings(cls, app_settings: Settings) -> RuntimeState:
        """Bootstrap runtime state from env-backed settings."""
        return cls(
            relay_backend_url=app_settings.relay_backend_url,
            relay_camera_id=app_settings.relay_camera_id,
            relay_auth_scheme=app_settings.relay_auth_scheme,
            relay_key_id=app_settings.relay_key_id,
            relay_private_key_pem=app_settings.relay_private_key_pem,
            local_relay_api_key=app_settings.local_relay_api_key,
            local_api_key=app_settings.local_api_key,
            authorized_api_keys=frozenset(app_settings.authorized_api_keys),
        )

    @property
    def relay_enabled(self) -> bool:
        """Whether relay credentials are currently available."""
        return bool(
            self.relay_backend_url
            and self.relay_camera_id
            and self.relay_auth_scheme == _DEVICE_ASSERTION_AUTH_SCHEME
            and self.relay_key_id
            and self.relay_private_key_pem
        )

    def set_relay_credentials(
        self,
        *,
        relay_backend_url: str,
        relay_camera_id: str,
        relay_auth_scheme: str,
        relay_key_id: str,
        relay_private_key_pem: str,
    ) -> None:
        """Replace the active relay credentials."""
        self.relay_backend_url = relay_backend_url
        self.relay_camera_id = relay_camera_id
        self.relay_auth_scheme = relay_auth_scheme
        self.relay_key_id = relay_key_id
        self.relay_private_key_pem = relay_private_key_pem
        if not self.local_relay_api_key:
            self.local_relay_api_key = f"LOCAL_{secrets.token_urlsafe(32)}"
        self.add_authorized_api_key(self.local_relay_api_key)

    def clear_relay_credentials(self) -> None:
        """Clear the active relay credentials without touching local keys."""
        self.relay_backend_url = ""
        self.relay_camera_id = ""
        self.relay_auth_scheme = ""
        self.relay_key_id = ""
        self.relay_private_key_pem = ""

    def set_local_api_key(self, key: str) -> None:
        """Set the persisted local API key."""
        self.local_api_key = key

    def replace_authorized_api_keys(self, keys: set[str] | frozenset[str]) -> None:
        """Atomically replace the current authorized-key snapshot."""
        self.authorized_api_keys = frozenset(keys)

    def add_authorized_api_key(self, key: str) -> None:
        """Add an authorized API key to the immutable snapshot."""
        if key in self.authorized_api_keys:
            return
        self.authorized_api_keys = frozenset({*self.authorized_api_keys, key})

    def is_authorized_api_key(self, api_key: str) -> bool:
        """Return whether a key is authorized in the current runtime snapshot."""
        return api_key in self.authorized_api_keys
