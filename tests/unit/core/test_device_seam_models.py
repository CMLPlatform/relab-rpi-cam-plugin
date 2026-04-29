"""Tests for shared backend<->plugin seam models."""

from __future__ import annotations

from relab_rpi_cam_models import (
    DeviceImageUploadAck,
    DevicePublicKeyJWK,
    LocalAccessInfo,
    PairingClaimedBootstrap,
    PairingPollResponse,
    PairingRegisterRequest,
    PairingStatus,
    RelayAuthScheme,
    RelayCommandEnvelope,
    RelayResponseEnvelope,
)

CAMERA_ID = "cam-1"


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
    bootstrap = PairingClaimedBootstrap(
        camera_id=CAMERA_ID,
        ws_url="wss://backend.example/v1/plugins/rpi-cam/ws/connect",
        auth_scheme=RelayAuthScheme.DEVICE_ASSERTION,
        key_id="kid-12345",
    )

    response = PairingPollResponse.from_claimed_bootstrap(bootstrap)

    assert response.status == PairingStatus.PAIRED
    assert response.camera_id == CAMERA_ID
    assert response.auth_scheme == RelayAuthScheme.DEVICE_ASSERTION


def test_local_access_info_and_upload_ack_round_trip() -> None:
    """Local access info and upload acks should stay tiny and stable."""
    local_access = LocalAccessInfo(
        local_api_key="LOCAL_123",
        candidate_urls=["http://192.168.1.20:8018"],
        mdns_name="pi.local",
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
