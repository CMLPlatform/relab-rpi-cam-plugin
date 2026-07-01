"""Tests for derived runtime access mode decisions."""

import pytest

from app.core.runtime_state import ConnectionMode, RuntimeState, connection_mode
from app.core.settings import Settings

PAIRING_BACKEND_URL = "https://api.example"


@pytest.mark.parametrize(
    ("runtime_credentials", "pairing_backend_url", "expected_mode"),
    [
        ("relay", PAIRING_BACKEND_URL, ConnectionMode.PAIRED),
        ("none", PAIRING_BACKEND_URL, ConnectionMode.PAIRING),
        ("none", "", ConnectionMode.IDLE),
    ],
)
def test_derived_connection_mode(
    runtime_credentials: str,
    pairing_backend_url: str,
    expected_mode: ConnectionMode,
) -> None:
    """Connection mode is derived from relay credentials first, then pairing config."""
    runtime_state = RuntimeState()
    if runtime_credentials == "relay":
        runtime_state.set_relay_credentials(
            relay_backend_url="wss://relay.example/ws",
            relay_camera_id="cam-1",
            relay_auth_scheme="device_assertion",
            relay_key_id="key-1",
            relay_private_key_pem="pem",
        )

    assert connection_mode(runtime_state, Settings(pairing_backend_url=pairing_backend_url)) is expected_mode
