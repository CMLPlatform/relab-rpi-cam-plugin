"""Capture delivery — getting captured JPEGs to their destination.

Two parts: pluggable image sinks (the storage abstraction) and a persistent
retry queue that drains failed pushes in the background.

See :mod:`app.delivery.base` for the ``ImageSink`` Protocol,
:mod:`app.delivery.factory` for ``get_image_sink``, and
:mod:`app.delivery.queue` for the retry queue and its worker.
"""

from app.delivery.base import ImageSink, ImageSinkError, StoredImage
from app.delivery.factory import ImageSinkConfigError, get_image_sink

__all__ = [
    "ImageSink",
    "ImageSinkConfigError",
    "ImageSinkError",
    "StoredImage",
    "get_image_sink",
]
