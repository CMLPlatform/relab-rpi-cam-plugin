"""Tests for shared backend<->plugin seam models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from relab_rpi_cam_models import (
    DeviceImageUploadAck,
    DevicePreviewThumbnailAck,
    DevicePublicKeyJWK,
    LocalAccessInfo,
    PairingClaimedBootstrap,
    PairingPollResponse,
    PairingRegisterRequest,
    PairingRegisterResponse,
    PairingStatus,
    RelayAuthScheme,
    RelayCommandEnvelope,
    RelayResponseEnvelope,
)

CAMERA_ID = "cam-1"
PAIRING_WS_URL = "wss://backend.example/v1/plugins/rpi-cam/ws/connect"
BACKEND_OWNED_RELAY_METHOD = "CONNECT"
BACKEND_OWNED_RELAY_PATH = "relative path checked by runtime"


def test_pairing_register_request_round_trips() -> None:
    """Pairing register payloads should serialize cleanly across repos."""
    request = PairingRegisterRequest(
        code="ABC123",
        rpi_fingerprint="fingerprint-123",
        public_key_jwk=DevicePublicKeyJWK(
            kty="EC",
            crv="P-256",
            x="x-value",
            y="y-value",
            kid="kid-12345",
        ),
        key_id="kid-12345",
    )

    restored = PairingRegisterRequest.model_validate_json(request.model_dump_json())

    assert restored == request


def test_pairing_poll_response_from_claimed_bootstrap() -> None:
    """The paired poll payload should derive cleanly from the bootstrap contract."""
    bootstrap = PairingClaimedBootstrap.model_validate(
        {
            "camera_id": CAMERA_ID,
            "ws_url": PAIRING_WS_URL,
            "auth_scheme": RelayAuthScheme.DEVICE_ASSERTION,
            "key_id": "kid-12345",
        }
    )

    response = PairingPollResponse.from_claimed_bootstrap(bootstrap)

    assert response.status == PairingStatus.PAIRED
    assert response.camera_id == CAMERA_ID
    assert response.auth_scheme == RelayAuthScheme.DEVICE_ASSERTION


def test_local_access_info_and_upload_ack_round_trip() -> None:
    """Local access info and upload acks should stay tiny and stable."""
    local_access = LocalAccessInfo.model_validate(
        {
            "local_api_key": "LOCAL_123",
            "candidate_urls": ["http://192.168.1.20:8018"],
            "mdns_name": "pi.local",
        }
    )
    upload_ack = DeviceImageUploadAck(image_id="a" * 32, image_url="/media/images/test.jpg")

    assert LocalAccessInfo.model_validate_json(local_access.model_dump_json()) == local_access
    assert DeviceImageUploadAck.model_validate_json(upload_ack.model_dump_json()) == upload_ack


def test_relay_command_and_response_envelopes_round_trip() -> None:
    """Relay envelopes should preserve request metadata and response payloads."""
    command = RelayCommandEnvelope(
        id="msg-1",
        method="GET",
        path="/camera",
        params={"include": "status"},
        headers={"traceparent": "00-abc-def-01"},
    )
    response = RelayResponseEnvelope(
        id="msg-1",
        status=200,
        content_type="application/json",
        data={"ok": True},
    )

    assert RelayCommandEnvelope.model_validate_json(command.model_dump_json()) == command
    assert RelayResponseEnvelope.model_validate_json(response.model_dump_json()) == response


def test_relay_command_envelope_allows_backend_owned_command_policy() -> None:
    """The shared relay command DTO validates wire shape, not command authorization policy."""
    command = RelayCommandEnvelope(
        id="msg-policy",
        method=BACKEND_OWNED_RELAY_METHOD,
        path=BACKEND_OWNED_RELAY_PATH,
        params={f"k{i}": i for i in range(33)},
        headers={"x" * 65: "ok"},
    )

    assert command.method == BACKEND_OWNED_RELAY_METHOD
    assert command.path == BACKEND_OWNED_RELAY_PATH


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "abc123", "expires_in": 600},
        {"code": "ABC123", "expires_in": 0},
    ],
)
def test_pairing_register_response_rejects_invalid_fields(payload: dict[str, object]) -> None:
    """Backend pairing register responses should match the expected code and TTL shape."""
    with pytest.raises(ValidationError):
        PairingRegisterResponse.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "camera_id": "../cam",
            "ws_url": PAIRING_WS_URL,
            "auth_scheme": RelayAuthScheme.DEVICE_ASSERTION,
            "key_id": "kid-12345",
        },
        {
            "camera_id": CAMERA_ID,
            "ws_url": "https://backend.example/ws",
            "auth_scheme": RelayAuthScheme.DEVICE_ASSERTION,
            "key_id": "kid-12345",
        },
        {
            "camera_id": CAMERA_ID,
            "ws_url": PAIRING_WS_URL,
            "auth_scheme": RelayAuthScheme.DEVICE_ASSERTION,
            "key_id": "bad/key",
        },
    ],
)
def test_pairing_claimed_bootstrap_rejects_invalid_fields(payload: dict[str, object]) -> None:
    """Claimed pairing credentials should be bounded and shaped before runtime use."""
    with pytest.raises(ValidationError):
        PairingClaimedBootstrap.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (DeviceImageUploadAck, {"image_id": "bad/id", "image_url": "/media/images/test.jpg"}),
        (DeviceImageUploadAck, {"image_id": "a" * 32, "image_url": "javascript:alert(1)"}),
        (DevicePreviewThumbnailAck, {"preview_thumbnail_url": "data:text/html,boom"}),
    ],
)
def test_upload_ack_models_reject_invalid_fields(
    model: type[DeviceImageUploadAck | DevicePreviewThumbnailAck],
    payload: dict[str, object],
) -> None:
    """Upload acknowledgements should keep image IDs and URLs within expected shapes."""
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_local_access_info_rejects_unbounded_values() -> None:
    """Local access info should stay small and URL-shaped."""
    with pytest.raises(ValidationError):
        LocalAccessInfo.model_validate(
            {
                "local_api_key": "",
                "candidate_urls": ["ftp://192.168.1.20:8018"],
                "mdns_name": "pi.local",
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "", "method": "GET", "path": "/camera"},
        {"id": "msg-1", "method": "GET", "path": "/camera", "extra": "field"},
    ],
)
def test_relay_command_envelope_rejects_invalid_wire_shape(payload: dict[str, object]) -> None:
    """Relay command envelopes should reject only malformed wire shapes."""
    with pytest.raises(ValidationError):
        RelayCommandEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "", "status": 200},
        {"id": "msg-1", "status": 600},
        {"id": "msg-1", "status": 200, "extra": "field"},
    ],
)
def test_relay_response_envelope_rejects_invalid_wire_shape(payload: dict[str, object]) -> None:
    """Relay response envelopes should stay small and HTTP-shaped."""
    with pytest.raises(ValidationError):
        RelayResponseEnvelope.model_validate(payload)
