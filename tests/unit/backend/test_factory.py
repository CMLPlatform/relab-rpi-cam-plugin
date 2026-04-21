"""Tests for the camera-backend factory."""

from __future__ import annotations

import pytest

from app.backend.factory import create_camera_backend
from app.camera.services.picamera2_backend import Picamera2Backend
from app.core.settings import settings

_DEFAULT_BACKEND_NAME = "picamera2"
_UNKNOWN_BACKEND_NAME = "unicorn"


def test_create_returns_picamera2_backend_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default setting ``camera_backend='picamera2'`` yields a Picamera2Backend."""
    monkeypatch.setattr(settings, "camera_backend", _DEFAULT_BACKEND_NAME)
    backend = create_camera_backend()
    assert isinstance(backend, Picamera2Backend)


def test_create_raises_on_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown backend names should fail loudly at startup rather than silently fall through."""
    monkeypatch.setattr(settings, "camera_backend", _UNKNOWN_BACKEND_NAME)
    with pytest.raises(ValueError, match="Unsupported camera backend: unicorn"):
        create_camera_backend()
