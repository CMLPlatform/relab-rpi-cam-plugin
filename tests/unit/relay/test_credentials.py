"""Tests for relay credential validation helpers."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.relay.credentials import load_relay_signing_private_key

_EXPECTED_RELAY_CURVE_NAME = "secp256r1"


def _private_key_pem(private_key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey) -> str:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def test_load_relay_signing_private_key_accepts_valid_p256_pem() -> None:
    """Relay signing keys should load only when they use the approved curve."""
    private_key = ec.generate_private_key(ec.SECP256R1())

    loaded = load_relay_signing_private_key(_private_key_pem(private_key))

    assert loaded.curve.name == _EXPECTED_RELAY_CURVE_NAME


def test_load_relay_signing_private_key_rejects_other_ec_curves() -> None:
    """Valid EC keys on non-approved curves should fail closed."""
    private_key = ec.generate_private_key(ec.SECP384R1())

    with pytest.raises(ValueError, match="P-256"):
        load_relay_signing_private_key(_private_key_pem(private_key))


def test_load_relay_signing_private_key_rejects_rsa_keys() -> None:
    """Non-EC private keys should not be accepted for device assertions."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(ValueError, match="P-256"):
        load_relay_signing_private_key(_private_key_pem(private_key))


def test_load_relay_signing_private_key_rejects_malformed_pem() -> None:
    """Malformed key material should produce a clear validation error."""
    with pytest.raises(ValueError, match="private key"):
        load_relay_signing_private_key("not a private key")
