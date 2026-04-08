"""Tests for stream data models."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import AnyUrl
from relab_rpi_cam_models.stream import Stream, StreamMetadata, StreamMode, StreamStateError


class TestStreamMetadata:
    """Tests for stream metadata helpers."""

    def test_fps_is_calculated_from_frame_duration(self) -> None:
        """FPS should be calculated as 1 / (frame_duration in seconds)."""
        metadata = StreamMetadata.from_metadata(
            {"Model": "mock"},
            {"FrameDuration": 33_333},
        )
        assert metadata.fps == pytest.approx(30.0, rel=0.01)

    def test_fps_is_none_when_frame_duration_missing(self) -> None:
        """FPS should be None if FrameDuration is missing."""
        metadata = StreamMetadata.from_metadata({"Model": "mock"}, {})
        assert metadata.fps is None


class TestStream:
    """Tests for the Stream dataclass."""

    def test_inactive_stream_returns_none(self) -> None:
        """get_info should return None if the stream is not active."""
        assert Stream().get_info({"Model": "mock"}, {"FrameDuration": 33_333}) is None

    def test_active_stream_returns_view(self) -> None:
        """get_info should return a StreamView with correct mode and metadata."""
        stream = Stream(
            mode=StreamMode.YOUTUBE,
            url=AnyUrl("https://youtube.com/watch?v=dQw4w9WgXcQ"),
            started_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        info = stream.get_info({"Model": "mock"}, {"FrameDuration": 33_333})
        assert info is not None
        assert info.mode == StreamMode.YOUTUBE
        assert info.metadata.fps == pytest.approx(30.0, rel=0.01)

    def test_missing_url_raises_stream_state_error(self) -> None:
        """get_info should raise StreamStateError if the stream is active but URL is missing."""
        stream = Stream(
            mode=StreamMode.YOUTUBE,
            url=None,
            started_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        with pytest.raises(StreamStateError):
            stream.get_info({"Model": "mock"}, {"FrameDuration": 33_333})

    def test_missing_started_at_raises_stream_state_error(self) -> None:
        """get_info should raise StreamStateError if the stream is active but started_at is missing."""
        stream = Stream(
            mode=StreamMode.YOUTUBE,
            url=AnyUrl("https://youtube.com/watch?v=dQw4w9WgXcQ"),
            started_at=None,
        )
        with pytest.raises(StreamStateError):
            stream.get_info({"Model": "mock"}, {"FrameDuration": 33_333})
