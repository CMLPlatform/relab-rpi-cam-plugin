"""Relay credential validation helpers."""

from __future__ import annotations

import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from relab_rpi_cam_models import RelayAuthScheme

_SAFE_RELAY_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]+$")
_MAX_RELAY_CAMERA_ID_LENGTH = 128
_MAX_RELAY_KEY_ID_LENGTH = 64
_RELAY_SIGNING_CURVE_NAME = "secp256r1"


def _validate_relay_identifier(value: str, *, field_name: str, max_length: int) -> None:
    """Validate relay identifiers before they are accepted as signing context."""
    if not value or len(value) > max_length or not _SAFE_RELAY_IDENTIFIER_PATTERN.fullmatch(value):
        msg = f"{field_name} must be 1-{max_length} URL-safe characters."
        raise ValueError(msg)


def load_relay_signing_private_key(private_key_pem: str) -> ec.EllipticCurvePrivateKey:
    """Load and validate the relay device-assertion private key."""
    try:
        private_key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    except (TypeError, ValueError) as exc:
        msg = "relay_private_key_pem must contain a valid PEM private key."
        raise ValueError(msg) from exc
    if not isinstance(private_key, ec.EllipticCurvePrivateKey) or private_key.curve.name != _RELAY_SIGNING_CURVE_NAME:
        msg = "relay_private_key_pem must be an EC P-256 private key."
        raise ValueError(msg)
    return private_key


def validate_relay_credentials(
    *,
    relay_camera_id: str,
    relay_auth_scheme: str,
    relay_key_id: str,
    relay_private_key_pem: str,
) -> None:
    """Validate relay device-assertion credentials at process trust boundaries."""
    if relay_auth_scheme != RelayAuthScheme.DEVICE_ASSERTION:
        msg = "relay_auth_scheme must be device_assertion."
        raise ValueError(msg)
    _validate_relay_identifier(
        relay_camera_id,
        field_name="relay_camera_id",
        max_length=_MAX_RELAY_CAMERA_ID_LENGTH,
    )
    _validate_relay_identifier(
        relay_key_id,
        field_name="relay_key_id",
        max_length=_MAX_RELAY_KEY_ID_LENGTH,
    )
    load_relay_signing_private_key(relay_private_key_pem)
