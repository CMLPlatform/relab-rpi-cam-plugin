"""Device assertion JWT minting for relay + backend-facing HTTPS calls.

The Pi holds an ECDSA P-256 private key (from pairing) and uses it to sign
short-lived JWTs that the backend verifies against the public JWK it stored
when pairing completed. The same audience is used for WebSocket relay auth and
outbound HTTPS image uploads — the backend's verification path is shared so
there's no point splitting audiences here.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import jwt

from app.core.runtime_context import get_active_runtime
from app.relay.credentials import load_relay_signing_private_key

DEVICE_ASSERTION_ALGORITHM = "ES256"
DEVICE_ASSERTION_AUDIENCE = "relab-rpi-cam-relay"
DEVICE_ASSERTION_ISSUER_PREFIX = "camera:"
DEVICE_ASSERTION_TTL_SECONDS = 120
DEVICE_ASSERTION_JTI_BYTES = 24


def build_device_assertion() -> str:
    """Mint a fresh short-lived ES256 device assertion for the current camera."""
    runtime_state = get_active_runtime().runtime_state
    now = int(datetime.now(UTC).timestamp())
    issuer = f"{DEVICE_ASSERTION_ISSUER_PREFIX}{runtime_state.relay_camera_id}"
    payload = {
        "iss": issuer,
        "sub": issuer,
        "aud": DEVICE_ASSERTION_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + DEVICE_ASSERTION_TTL_SECONDS,
        "jti": secrets.token_urlsafe(DEVICE_ASSERTION_JTI_BYTES),
    }
    return jwt.encode(
        payload,
        load_relay_signing_private_key(runtime_state.relay_private_key_pem),
        algorithm=DEVICE_ASSERTION_ALGORITHM,
        headers={"kid": runtime_state.relay_key_id},
    )
