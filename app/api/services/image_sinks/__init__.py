"""Pluggable image sinks — the Pi's storage abstraction for captured JPEGs.

See :mod:`app.api.services.image_sinks.base` for the ``ImageSink`` Protocol
and :mod:`app.api.services.image_sinks.factory` for ``get_image_sink``.
"""

from app.api.services.image_sinks.backend_sink import BackendPushSink
from app.api.services.image_sinks.base import ImageSink, ImageSinkError, StoredImage
from app.api.services.image_sinks.factory import get_image_sink
from app.api.services.image_sinks.s3_sink import S3CompatibleSink

__all__ = [
    "BackendPushSink",
    "ImageSink",
    "ImageSinkError",
    "S3CompatibleSink",
    "StoredImage",
    "get_image_sink",
]
