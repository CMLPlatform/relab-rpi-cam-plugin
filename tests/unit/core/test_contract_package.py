"""Tests for the shared contract package surface."""

from relab_rpi_cam_models.images import CameraProperties, CaptureMetadata
from relab_rpi_cam_models.stream import StreamMetadata

import relab_rpi_cam_models as contracts


def test_shared_package_exports_only_contract_types() -> None:
    """The package should not expose plugin runtime or workflow internals."""
    assert hasattr(contracts, "StreamView")
    assert not hasattr(contracts, "Stream")
    assert not hasattr(contracts, "YoutubeStreamConfig")


def test_stream_metadata_remains_constructible_as_dto() -> None:
    """Shared DTOs should still validate payloads used across the repo boundary."""
    metadata = StreamMetadata(
        camera_properties=CameraProperties.model_validate({"Model": "mock"}),
        capture_metadata=CaptureMetadata.model_validate({"FrameDuration": 33_333}),
    )
    assert metadata.fps is not None
    assert metadata.model_dump()["fps"] is not None
