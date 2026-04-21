"""Tests for the plugin's API exception classes."""

from __future__ import annotations

from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode

from app.camera.exceptions import ActiveStreamError, CameraInitializationError
from app.media.stream_state import ActiveStreamState
from tests.constants import YOUTUBE_WATCH_URL_PREFIX

_STREAM_ACTIVE_PREFIX = "Stream active in"
_STREAM_STOP_HINT = "Stop streaming first"
_CAMERA_DEVICE_3 = "camera device 3"
_CAMERA_DEVICE_0 = "camera device 0"
_SENSOR_TIMEOUT = "sensor timeout"


class TestActiveStreamError:
    """Raised when the caller tries to touch a camera that is already streaming."""

    def test_message_includes_mode_and_url(self) -> None:
        """Error message embeds the active stream's mode and URL."""
        stream = ActiveStreamState(mode=StreamMode.YOUTUBE, url=AnyUrl(f"{YOUTUBE_WATCH_URL_PREFIX}abc"))
        err = ActiveStreamError(stream)
        assert err.mode == StreamMode.YOUTUBE
        assert str(err.url).startswith(YOUTUBE_WATCH_URL_PREFIX)
        assert _STREAM_ACTIVE_PREFIX in str(err)
        assert _STREAM_STOP_HINT in str(err)


class TestCameraInitializationError:
    """Two branches: with and without an optional reason suffix."""

    def test_bare_message_without_reason(self) -> None:
        """Without ``reason`` the message is just the device number."""
        err = CameraInitializationError(camera_num=3)
        assert _CAMERA_DEVICE_3 in str(err)
        # No trailing ``: reason`` section when reason is empty.
        assert not str(err).endswith(":")

    def test_message_appends_reason_when_provided(self) -> None:
        """When ``reason`` is supplied the message appends it as a suffix."""
        err = CameraInitializationError(camera_num=0, reason=_SENSOR_TIMEOUT)
        assert _CAMERA_DEVICE_0 in str(err)
        assert str(err).endswith(_SENSOR_TIMEOUT)
