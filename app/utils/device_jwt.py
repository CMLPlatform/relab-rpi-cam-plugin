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

from app.core.config import settings

DEVICE_ASSERTION_AUDIENCE = "relab-rpi-cam-relay"
DEVICE_ASSERTION_TTL_SECONDS = 120


def build_device_assertion() -> str:
    """Mint a fresh short-lived ES256 device assertion for the current camera."""
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "iss": f"camera:{settings.relay_camera_id}",
        "sub": f"camera:{settings.relay_camera_id}",
        "aud": DEVICE_ASSERTION_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + DEVICE_ASSERTION_TTL_SECONDS,
        "jti": secrets.token_urlsafe(24),
    }
    return jwt.encode(
        payload,
        settings.relay_private_key_pem,
        algorithm="ES256",
        headers={"kid": settings.relay_key_id},
    )
