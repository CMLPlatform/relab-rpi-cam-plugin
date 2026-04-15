"""Public contract surface for RELab Raspberry Pi camera integrations."""

from .camera import CameraMode, CameraStatusView
from .device_seam import (
    SAFE_RELAY_TRACE_HEADERS,
    DeviceImageUploadAck,
    DevicePublicKeyJWK,
    LocalAccessInfo,
    PairingClaimedBootstrap,
    PairingClaimedRecord,
    PairingPendingRecord,
    PairingPollResponse,
    PairingRegisterRequest,
    PairingRegisterResponse,
    PairingStatus,
    RelayAuthScheme,
    RelayCommandEnvelope,
    RelayMessageType,
    RelayResponseEnvelope,
)
from .images import (
    BaseMetadata,
    CameraProperties,
    CaptureMetadata,
    ImageCaptureResponse,
    ImageCaptureStatus,
    ImageMetadata,
    ImageProperties,
)
from .stream import StreamMetadata, StreamMode, StreamView
from .telemetry import TelemetrySnapshot, ThermalState

__all__ = [
    "SAFE_RELAY_TRACE_HEADERS",
    "BaseMetadata",
    "CameraMode",
    "CameraProperties",
    "CameraStatusView",
    "CaptureMetadata",
    "DeviceImageUploadAck",
    "DevicePublicKeyJWK",
    "ImageCaptureResponse",
    "ImageCaptureStatus",
    "ImageMetadata",
    "ImageProperties",
    "LocalAccessInfo",
    "PairingClaimedBootstrap",
    "PairingClaimedRecord",
    "PairingPendingRecord",
    "PairingPollResponse",
    "PairingRegisterRequest",
    "PairingRegisterResponse",
    "PairingStatus",
    "RelayAuthScheme",
    "RelayCommandEnvelope",
    "RelayMessageType",
    "RelayResponseEnvelope",
    "StreamMetadata",
    "StreamMode",
    "StreamView",
    "TelemetrySnapshot",
    "ThermalState",
]
