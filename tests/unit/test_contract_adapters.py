"""Tests for plugin-owned adapters around shared contract DTOs."""

from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode

from app.api.services.contract_adapters import (
    build_image_metadata,
    build_stream_metadata,
    image_metadata_to_exif,
)
from app.api.services.stream_state import ActiveStreamState, StreamStateError

MOCK_CAMERA_MODEL = "mock"
YOUTUBE_PROVIDER = "youtube"


class TestContractAdapters:
    """Tests for plugin helpers that assemble shared contract DTOs."""

    def test_build_stream_metadata_calculates_fps(self) -> None:
        """FPS should be calculated from frame duration."""
        metadata = build_stream_metadata({"Model": MOCK_CAMERA_MODEL}, {"FrameDuration": 33_333})
        assert metadata.fps == pytest.approx(30.0, rel=0.01)

    def test_build_stream_metadata_handles_missing_frame_duration(self) -> None:
        """FPS should be None when frame duration is missing."""
        metadata = build_stream_metadata({"Model": MOCK_CAMERA_MODEL}, {})
        assert metadata.fps is None

    def test_build_image_metadata_creates_contract_dto(self) -> None:
        """Image captures should be adapted into the shared DTO."""
        image = Image.new("RGB", (320, 240), color="red")
        metadata = build_image_metadata(image, {"Model": MOCK_CAMERA_MODEL}, {"ExposureTime": 1_000})
        assert metadata.image_properties.width == 320
        assert metadata.image_properties.height == 240
        assert metadata.camera_properties.camera_model == MOCK_CAMERA_MODEL

    def test_image_metadata_to_exif_adds_required_tags(self) -> None:
        """Plugin-local EXIF conversion should remain outside the shared package."""
        image = Image.new("RGB", (100, 100), color="red")
        metadata = build_image_metadata(image, {"Model": MOCK_CAMERA_MODEL}, {"ExposureTime": 1_000})
        exif = image_metadata_to_exif(metadata)
        assert exif


class TestActiveStreamState:
    """Tests for plugin-local runtime stream state."""

    def test_inactive_stream_returns_none(self) -> None:
        """Inactive runtime state should not produce a response DTO."""
        assert (
            ActiveStreamState().to_view({"Model": MOCK_CAMERA_MODEL}, {"FrameDuration": 33_333}) is None
        )

    def test_active_stream_returns_contract_view(self) -> None:
        """Active runtime state should adapt into a shared stream view."""
        state = ActiveStreamState(
            mode=StreamMode.YOUTUBE,
            url=AnyUrl("https://youtube.com/watch?v=dQw4w9WgXcQ"),
            started_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        info = state.to_view({"Model": MOCK_CAMERA_MODEL}, {"FrameDuration": 33_333})
        assert info is not None
        assert info.mode == StreamMode.YOUTUBE
        assert info.provider == YOUTUBE_PROVIDER
        assert info.metadata.fps == pytest.approx(30.0, rel=0.01)

    def test_missing_url_raises_stream_state_error(self) -> None:
        """Incomplete runtime state should still fail loudly."""
        state = ActiveStreamState(
            mode=StreamMode.YOUTUBE,
            url=None,
            started_at=datetime.now(UTC),
        )
        with pytest.raises(StreamStateError):
            state.to_view({"Model": MOCK_CAMERA_MODEL}, {"FrameDuration": 33_333})
