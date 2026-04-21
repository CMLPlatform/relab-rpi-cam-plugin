"""Factory for selecting the configured camera backend."""

from app.camera.services.backend import CameraBackend
from app.camera.services.picamera2_backend import Picamera2Backend
from app.core.settings import settings

_PICAMERA2_BACKEND = "picamera2"


def create_camera_backend() -> CameraBackend:
    """Instantiate the configured camera backend."""
    if settings.camera_backend == _PICAMERA2_BACKEND:
        return Picamera2Backend()
    msg = f"Unsupported camera backend: {settings.camera_backend}"
    raise ValueError(msg)
