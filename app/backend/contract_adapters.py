"""Helpers for converting plugin runtime data into shared contract DTOs."""

from collections.abc import Mapping
from typing import Any

from PIL.ExifTags import IFD, Base
from PIL.Image import Exif, Image
from r9cam_models.images import (
    CameraProperties,
    CaptureMetadata,
    ImageMetadata,
    ImageProperties,
)
from r9cam_models.stream import StreamMetadata

from app.__version__ import version


def build_image_metadata(
    img: Image,
    camera_properties: Mapping[str, Any],
    capture_metadata: Mapping[str, Any],
) -> ImageMetadata:
    """Build the shared image metadata DTO from runtime capture data."""
    return ImageMetadata(
        image_properties=ImageProperties(width=img.size[0], height=img.size[1]),
        camera_properties=CameraProperties.model_validate(camera_properties),
        capture_metadata=CaptureMetadata.model_validate(capture_metadata),
    )


def build_stream_metadata(
    camera_properties: Mapping[str, Any],
    capture_metadata: Mapping[str, Any],
) -> StreamMetadata:
    """Build the shared stream metadata DTO from runtime capture data."""
    return StreamMetadata(
        camera_properties=CameraProperties.model_validate(camera_properties),
        capture_metadata=CaptureMetadata.model_validate(capture_metadata),
    )


def image_metadata_to_exif(metadata: ImageMetadata) -> Exif:
    """Convert shared image metadata into the EXIF block written by the plugin.

    Image-level tags go in IFD0; capture tags go in the Exif sub-IFD, where EXIF
    readers look for them. ``capture_time`` is UTC, so OffsetTime is ``+00:00``.
    """
    exif = Exif()
    exif.update(
        {
            Base.Make.value: "Raspberry Pi",
            Base.Software.value: f"r9cam {version}",
            Base.DateTime.value: metadata.image_properties.capture_time.strftime("%Y:%m:%d %H:%M:%S"),
            Base.ImageWidth.value: metadata.image_properties.width,
            Base.ImageLength.value: metadata.image_properties.height,
        }
    )
    if metadata.camera_properties.camera_model is not None:
        exif[Base.Model.value] = metadata.camera_properties.camera_model

    sub_ifd = exif.get_ifd(IFD.Exif)
    sub_ifd[Base.OffsetTime.value] = "+00:00"
    if metadata.capture_metadata.exposure_time is not None:
        sub_ifd[Base.ExposureTime.value] = metadata.capture_metadata.exposure_time / 1_000_000
    if metadata.capture_metadata.sensor_temperature is not None:
        # NOTE: libcamera reports the sensor die temperature, not the ambient air temperature.
        sub_ifd[Base.AmbientTemperature.value] = metadata.capture_metadata.sensor_temperature

    return exif
