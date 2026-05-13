"""Relay credential validation helpers."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

_RELAY_SIGNING_CURVE_NAME = "secp256r1"


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
