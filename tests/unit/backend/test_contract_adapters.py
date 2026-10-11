"""Tests for plugin-owned adapters around shared contract DTOs."""

from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from PIL import Image
from PIL.ExifTags import IFD, Base
from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode

from app.__version__ import version
from app.backend.contract_adapters import (
    build_image_metadata,
    build_stream_metadata,
    image_metadata_to_exif,
)
from app.camera.streaming.stream_state import ActiveStreamState, StreamStateError
from tests.constants import YOUTUBE_WATCH_URL_PREFIX

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

    def test_build_image_metadata_reads_libcamera_spelled_controls(self) -> None:
        """Picamera2 metadata uses libcamera's British spellings, e.g. ColourTemperature."""
        image = Image.new("RGB", (100, 100), color="red")
        capture_metadata = {
            "ExposureTime": 1_000,
            "FrameDuration": 33_333,
            "ColourTemperature": 5_000,
            "ColourGains": (1.8, 1.5),
            "AnalogueGain": 1.0,
            "DigitalGain": 1.0,
            "Lux": 400.0,
            "SensorTemperature": 42.0,
        }
        metadata = build_image_metadata(image, {"Model": MOCK_CAMERA_MODEL}, capture_metadata)
        assert metadata.capture_metadata.color_temperature == 5_000
        assert metadata.capture_metadata.analogue_gain == 1.0
        assert metadata.capture_metadata.lux == 400.0

    def test_image_metadata_to_exif_adds_required_tags(self) -> None:
        """Plugin-local EXIF conversion should remain outside the shared package."""
        image = Image.new("RGB", (100, 100), color="red")
        metadata = build_image_metadata(image, {"Model": MOCK_CAMERA_MODEL}, {"ExposureTime": 1_000})
        exif = image_metadata_to_exif(metadata)
        assert exif[Base.Make.value] == "Raspberry Pi"
        assert exif[Base.Software.value] == f"relab-rpi-cam-plugin {version}"
        assert exif[Base.ImageWidth.value] == 100
        assert exif[Base.ImageLength.value] == 100

    def test_image_metadata_to_exif_preserves_zero_sensor_temperature(self) -> None:
        """A valid 0.0 degC sensor reading must not be dropped by a falsy check."""
        image = Image.new("RGB", (100, 100), color="red")
        metadata = build_image_metadata(image, {"Model": MOCK_CAMERA_MODEL}, {"SensorTemperature": 0.0})
        exif = image_metadata_to_exif(metadata)
        assert exif.get_ifd(IFD.Exif)[Base.AmbientTemperature.value] == 0.0

    def test_image_metadata_to_exif_survives_jpeg_round_trip(self) -> None:
        """Tags must sit in the IFD that EXIF readers look in, after a real JPEG encode."""
        image = Image.new("RGB", (100, 100), color="red")
        metadata = build_image_metadata(
            image, {"Model": MOCK_CAMERA_MODEL}, {"ExposureTime": 1_000, "ColourTemperature": 5_000}
        )
        buffer = BytesIO()
        image.save(buffer, exif=image_metadata_to_exif(metadata), format="JPEG")
        exif = Image.open(buffer).getexif()
        sub_ifd = exif.get_ifd(IFD.Exif)

        assert exif[Base.Model.value] == MOCK_CAMERA_MODEL
        assert exif[Base.Software.value] == f"relab-rpi-cam-plugin {version}"
        assert Base.ExposureTime.value not in exif
        assert sub_ifd[Base.ExposureTime.value] == pytest.approx(0.001)
        assert sub_ifd[Base.OffsetTime.value] == "+00:00"
        # A colour temperature is reported under auto white balance too, so it says nothing about the mode.
        assert Base.WhiteBalance.value not in sub_ifd


class TestActiveStreamState:
    """Tests for plugin-local runtime stream state."""

    def test_inactive_stream_returns_none(self) -> None:
        """Inactive runtime state should not produce a response DTO."""
        assert ActiveStreamState().to_view({"Model": MOCK_CAMERA_MODEL}, {"FrameDuration": 33_333}) is None

    def test_active_stream_returns_contract_view(self) -> None:
        """Active runtime state should adapt into a shared stream view."""
        state = ActiveStreamState(
            mode=StreamMode.YOUTUBE,
            url=AnyUrl(f"{YOUTUBE_WATCH_URL_PREFIX}dQw4w9WgXcQ"),
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
