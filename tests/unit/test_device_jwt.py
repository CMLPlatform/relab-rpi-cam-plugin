"""Tests for the short-lived ES256 device-assertion JWT builder.

The ``build_device_assertion`` function is used by both the WebSocket relay
auth path and the outbound image-upload HTTPS calls. The output must be a
valid ES256 JWT with the shared ``relab-rpi-cam-relay`` audience, a unique
jti, and the ``kid`` header set to the camera's relay key id.
"""

from __future__ import annotations

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.config import settings
from app.utils.device_jwt import (
    DEVICE_ASSERTION_AUDIENCE,
    DEVICE_ASSERTION_TTL_SECONDS,
    build_device_assertion,
)

_EXPECTED_ALG = "ES256"
_KID_DEFAULT = "cam-key-42"
_CAMERA_ID_DEFAULT = "11111111-2222-3333-4444-555555555555"
_CAMERA_ID_ROUND_TRIP = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_ISS_DEFAULT = f"camera:{_CAMERA_ID_DEFAULT}"
_ISS_ROUND_TRIP = f"camera:{_CAMERA_ID_ROUND_TRIP}"


def _fresh_p256_pem() -> str:
    """Mint a throwaway P-256 private key in PEM form for signing."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def _signing_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a valid signing key + identity on the settings singleton."""
    pem = _fresh_p256_pem()
    monkeypatch.setattr(settings, "relay_private_key_pem", pem)
    monkeypatch.setattr(settings, "relay_key_id", _KID_DEFAULT)
    monkeypatch.setattr(settings, "relay_camera_id", _CAMERA_ID_DEFAULT)


@pytest.mark.usefixtures("_signing_settings")
class TestBuildDeviceAssertion:
    """Shape assertions for the minted JWT."""

    def test_token_header_uses_es256_and_kid(self) -> None:
        """The JOSE header declares ES256 and points ``kid`` at the camera's relay key."""
        token = build_device_assertion()
        header = jwt.get_unverified_header(token)
        assert header["alg"] == _EXPECTED_ALG
        assert header["kid"] == _KID_DEFAULT

    def test_token_payload_has_expected_claims(self) -> None:
        """All the claims the backend relies on are present with sane values."""
        token = build_device_assertion()
        # Decode without verifying signature; the signer is a fresh throwaway key.
        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["aud"] == DEVICE_ASSERTION_AUDIENCE
        assert payload["iss"] == _ISS_DEFAULT
        assert payload["sub"] == payload["iss"]
        assert payload["exp"] - payload["iat"] == DEVICE_ASSERTION_TTL_SECONDS
        assert payload["nbf"] == payload["iat"]
        # jti is a random token — non-empty string.
        assert isinstance(payload["jti"], str)
        assert len(payload["jti"]) > 0

    def test_successive_tokens_have_distinct_jtis(self) -> None:
        """Each call should mint a fresh ``jti`` so the backend can reject replays."""
        first = jwt.decode(build_device_assertion(), options={"verify_signature": False})
        second = jwt.decode(build_device_assertion(), options={"verify_signature": False})
        assert first["jti"] != second["jti"]


class TestBuildDeviceAssertionRoundTrip:
    """Round-trip: sign with a fresh private key, verify with its public counterpart."""

    def test_token_is_verifiable_with_matching_public_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Round-trip: sign with the installed private key, verify with its public counterpart."""
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        public_pem = (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        monkeypatch.setattr(settings, "relay_private_key_pem", private_pem)
        monkeypatch.setattr(settings, "relay_key_id", "cam-key-round-trip")
        monkeypatch.setattr(settings, "relay_camera_id", _CAMERA_ID_ROUND_TRIP)

        token = build_device_assertion()
        decoded = jwt.decode(token, public_pem, algorithms=[_EXPECTED_ALG], audience=DEVICE_ASSERTION_AUDIENCE)

        assert decoded["iss"] == _ISS_ROUND_TRIP
