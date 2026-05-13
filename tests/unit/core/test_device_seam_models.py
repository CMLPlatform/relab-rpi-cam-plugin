"""Tests for shared backend<->plugin seam models."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from relab_rpi_cam_models import (
    PAIRING_CODE_ALPHABET,
    PAIRING_CODE_LENGTH,
    PAIRING_CODE_PATTERN,
    DeviceImageUploadAck,
    DevicePreviewThumbnailAck,
    DevicePublicKeyJWK,
    LocalAccessInfo,
    PairingClaimedBootstrap,
    PairingCode,
    PairingPollResponse,
    PairingRegisterRequest,
    PairingRegisterResponse,
    PairingStatus,
    RelayAuthScheme,
    RelayCommandEnvelope,
    RelayResponseEnvelope,
    build_relay_command,
    extract_safe_relay_headers,
    relay_command_is_allowed,
    relay_content_type_is_binary,
)

CAMERA_ID = "cam-1"
PAIRING_WS_URL = "wss://backend.example/v1/plugins/rpi-cam/ws/connect"
BACKEND_OWNED_RELAY_METHOD = "CONNECT"
BACKEND_OWNED_RELAY_PATH = "relative path checked by runtime"
RELAY_GET_METHOD = "GET"
EXPECTED_PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
EXPECTED_PAIRING_CODE_PATTERN = r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}$"
VALID_PAIRING_CODE = "ABC234"


def test_pairing_code_contract_is_public_and_validates_unambiguous_codes() -> None:
    """The shared package should be the source of truth for pairing-code validation."""
    assert PAIRING_CODE_ALPHABET == EXPECTED_PAIRING_CODE_ALPHABET
    assert PAIRING_CODE_LENGTH == 6
    assert PAIRING_CODE_PATTERN == EXPECTED_PAIRING_CODE_PATTERN
    assert TypeAdapter(PairingCode).validate_python(VALID_PAIRING_CODE) == VALID_PAIRING_CODE


@pytest.mark.parametrize("code", ["abc234", "ABC230", "ABC23O", "ABC23I", "ABC231", "ABC23", "ABC2345"])
def test_pairing_code_contract_rejects_ambiguous_or_malformed_codes(code: str) -> None:
    """The public PairingCode type should reject hard-to-read and malformed codes."""
    with pytest.raises(ValidationError):
        TypeAdapter(PairingCode).validate_python(code)


def test_pairing_register_request_round_trips() -> None:
    """Pairing register payloads should serialize cleanly across repos."""
    request = PairingRegisterRequest(
        code="ABC234",
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


@pytest.mark.parametrize("code", ["abc234", "ABC230", "ABC23O", "ABC23I", "ABC231", "ABC23", "ABC2345"])
def test_pairing_register_request_rejects_ambiguous_or_malformed_codes(code: str) -> None:
    """Pairing codes should stay six friendly, unambiguous characters."""
    with pytest.raises(ValidationError):
        PairingRegisterRequest(
            code=code,
            rpi_fingerprint="fingerprint-123",
            public_key_jwk=DevicePublicKeyJWK(kty="EC", crv="P-256", x="x-value", y="y-value", kid="kid-12345"),
            key_id="kid-12345",
        )


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


def test_relay_helpers_build_envelopes_and_filter_safe_headers() -> None:
    """Shared helpers should build the wire envelopes and keep only safe trace headers."""
    command = build_relay_command(
        "msg-1",
        "get",
        "/camera",
        params={"include": "status"},
        headers={"TraceParent": "parent", "Authorization": "Bearer no"},
    )
    assert command.method == RELAY_GET_METHOD
    assert command.headers == {"traceparent": "parent"}
    assert extract_safe_relay_headers({"TraceState": "state", "x-test": "no", "baggage": 123}) == {
        "tracestate": "state"
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/camera"),
        ("GET", "/camera/controls"),
        ("PATCH", "/camera/controls"),
        ("PUT", "/camera/focus"),
        ("POST", "/preview/start"),
        ("POST", "/preview/stop"),
        ("GET", "/camera%2Fcontrols"),
        ("GET", "/preview/hls/cam-preview/index.m3u8"),
    ],
)
def test_relay_command_policy_accepts_shared_allowlist(method: str, path: str) -> None:
    """Backend and Pi should agree on every relay command that may cross the seam."""
    assert relay_command_is_allowed(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "camera"),
        ("GET", "/preview/hls"),
        ("GET", "/preview/hls/../private/segment.mp4"),
        ("GET", "/preview/hls/%2e%2e/private/segment.mp4"),
        ("GET", "/preview/hls/%252e%252e/private/segment.mp4"),
        ("GET", "/preview/hls/%252525252e%252525252e/private/segment.mp4"),
        ("GET", "/camera/../system/telemetry"),
        ("GET", "/camera/%2e%2e/system/telemetry"),
        ("GET", "/camera/%252e%252e/system/telemetry"),
        ("POST", "/preview/hls/cam-preview/index.m3u8"),
        ("DELETE", "/camera"),
        ("POST", "/auth/login"),
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("GET", "/system/config"),
        ("POST", "/system/telemetry"),
        ("GET", "/metrics"),
        ("GET", "/local-key"),
        ("POST", "/pairing/code"),
    ],
)
def test_relay_command_policy_rejects_out_of_scope_commands(method: str, path: str) -> None:
    """Shared command policy should reject relative paths and non-allowlisted operations."""
    assert not relay_command_is_allowed(method, path)


@pytest.mark.parametrize(
    "content_type",
    [
        "image/jpeg",
        "application/octet-stream",
        "application/octet-stream; charset=binary",
        "video/mp4",
        "video/iso.segment; charset=binary",
    ],
)
def test_relay_content_type_binary_detection(content_type: str) -> None:
    """Shared binary detection should cover images, opaque bytes, and HLS video segments."""
    assert relay_content_type_is_binary(content_type)


def test_relay_content_type_binary_detection_rejects_text() -> None:
    """Text and JSON responses should remain in the JSON relay envelope."""
    assert not relay_content_type_is_binary("application/json")
    assert not relay_content_type_is_binary("application/vnd.apple.mpegurl")
    assert not relay_content_type_is_binary("application/not-octet-stream")


def test_relay_command_envelope_allows_backend_owned_command_policy() -> None:
    """The shared relay command DTO validates wire shape, not command authorization policy."""
    command = RelayCommandEnvelope(
        id="msg-policy",
        method=BACKEND_OWNED_RELAY_METHOD,
        path=BACKEND_OWNED_RELAY_PATH,
        params={"page": 1},
        headers={"traceparent": "00-abc-def-01"},
    )

    assert command.method == BACKEND_OWNED_RELAY_METHOD
    assert command.path == BACKEND_OWNED_RELAY_PATH


@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": "msg-params",
            "method": "GET",
            "path": "/camera",
            "params": {f"k{i}": i for i in range(17)},
        },
        {
            "id": "msg-param-name",
            "method": "GET",
            "path": "/camera",
            "params": {"x" * 65: "value"},
        },
        {
            "id": "msg-param-value",
            "method": "GET",
            "path": "/camera",
            "params": {"include": {"nested": "no"}},
        },
        {
            "id": "msg-param-nan",
            "method": "GET",
            "path": "/camera",
            "params": {"value": float("nan")},
        },
        {
            "id": "msg-headers",
            "method": "GET",
            "path": "/camera",
            "headers": {f"x-{i}": "value" for i in range(17)},
        },
        {
            "id": "msg-header-name",
            "method": "GET",
            "path": "/camera",
            "headers": {"x" * 65: "value"},
        },
        {
            "id": "msg-header-value",
            "method": "GET",
            "path": "/camera",
            "headers": {"traceparent": "x" * 513},
        },
    ],
)
def test_relay_command_envelope_rejects_unbounded_params_and_headers(payload: dict[str, object]) -> None:
    """Relay params and headers should stay bounded before dispatch to httpx."""
    with pytest.raises(ValidationError):
        RelayCommandEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "abc123", "expires_in": 600},
        {"code": "ABC230", "expires_in": 600},
        {"code": "ABC23O", "expires_in": 600},
        {"code": "ABC23I", "expires_in": 600},
        {"code": "ABC231", "expires_in": 600},
        {"code": "ABC23", "expires_in": 600},
        {"code": "ABC2345", "expires_in": 600},
        {"code": "ABC234", "expires_in": 0},
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
