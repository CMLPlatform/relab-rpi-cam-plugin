"""Pure-helper tests for picamera2_backend.

These tests exercise the side-effect-free helpers in ``picamera2_backend`` —
focus-mode parsers, value normalisation, control-option lookups, and the
Picamera2 metadata serialiser. None of them touch real libcamera/picamera2
hardware, so they run identically on macOS and on a Pi.

A real on-Pi test file (``test_picamera2_backend.py``) stays skip-on-import so
the hardware-bound methods remain covered there when libcamera is actually
available.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.camera import CameraMode
from relab_rpi_cam_models.stream import StreamMode

from app.camera.exceptions import CameraInitializationError
from app.camera.schemas import FocusControlRequest, FocusMode, YoutubeStreamConfig
from app.camera.services import picamera2_backend as backend_mod
from app.camera.services.picamera2_backend import (
    Picamera2Backend,
    _af_mode_auto,
    _af_mode_continuous,
    _af_mode_manual,
    _control_options,
    _focus_mode_to_af_mode,
    _normalize_control_value,
    _serialize_mapping,
    _to_json_value,
    _value_type,
)
from tests.constants import (
    CAMERA_DEVICE_NOT_FOUND,
    PICAMERA2_CAM_HIRES_PATH,
    PICAMERA2_CAMERA_NOT_INITIALIZED,
    PICAMERA2_MAIN_STREAM_NAME,
    PICAMERA2_STARTUP_TIMEOUT,
    YOUTUBE_PUBLIC_URL,
)

# String literals used across the assertions — hoisted to module scope so ruff
# doesn't flag them as magic values and regressions stay easy to spot.
_LIBCAM_MANUAL = "libcam-manual"
_LIBCAM_AUTO = "libcam-auto"
_LIBCAM_CONTINUOUS = "libcam-continuous"
_OPAQUE_REPR = "opaque-repr"
_ENUM_LABEL = "enum"
_OPAQUE_CLASS_NAME = "_Opaque"
_FAKE_ENUM_WARM_NAME = "WARM"
_FAKE_ENUM_NORMAL_NAME = "NORMAL"


# ── _af_mode_* fallbacks ──────────────────────────────────────────────────────


class TestAfModeHelpers:
    """Focus-mode helpers should fall back to integer enums when libcamera is absent."""

    def test_manual_returns_int_when_controls_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without libcamera the manual mode falls back to the int 0."""
        monkeypatch.setattr(backend_mod, "controls", None)
        assert _af_mode_manual() == 0

    def test_auto_returns_int_when_controls_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without libcamera the auto mode falls back to the int 1."""
        monkeypatch.setattr(backend_mod, "controls", None)
        assert _af_mode_auto() == 1

    def test_continuous_returns_int_when_controls_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without libcamera the continuous mode falls back to the int 2."""
        monkeypatch.setattr(backend_mod, "controls", None)
        assert _af_mode_continuous() == 2

    def test_helpers_use_libcamera_enums_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a fake libcamera ``controls`` module is installed, the helpers use its enums."""
        fake_controls = MagicMock()
        fake_controls.AfModeEnum.Manual = _LIBCAM_MANUAL
        fake_controls.AfModeEnum.Auto = _LIBCAM_AUTO
        fake_controls.AfModeEnum.Continuous = _LIBCAM_CONTINUOUS
        monkeypatch.setattr(backend_mod, "controls", fake_controls)

        assert _af_mode_manual() == _LIBCAM_MANUAL
        assert _af_mode_auto() == _LIBCAM_AUTO
        assert _af_mode_continuous() == _LIBCAM_CONTINUOUS


# ── _focus_mode_to_af_mode ────────────────────────────────────────────────────


class TestFocusModeToAfMode:
    """The friendly/enum-string -> AfModeEnum mapper."""

    @pytest.fixture(autouse=True)
    def _no_libcamera(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Keep the int-fallback path so assertions stay deterministic.
        monkeypatch.setattr(backend_mod, "controls", None)

    def test_plain_friendly_string(self) -> None:
        """Lowercase friendly names map to the int enums."""
        assert _focus_mode_to_af_mode("manual") == 0
        assert _focus_mode_to_af_mode("auto") == 1
        assert _focus_mode_to_af_mode("continuous") == 2

    def test_case_insensitive(self) -> None:
        """Mixed and upper case names are tolerated."""
        assert _focus_mode_to_af_mode("Manual") == 0
        assert _focus_mode_to_af_mode("AUTO") == 1
        assert _focus_mode_to_af_mode("Continuous") == 2

    def test_libcamera_enum_string_prefix_stripped(self) -> None:
        """``AfModeEnum.Manual`` should collapse to the bare ``manual`` branch."""
        assert _focus_mode_to_af_mode("AfModeEnum.Manual") == 0
        assert _focus_mode_to_af_mode("AfModeEnum.Auto") == 1
        assert _focus_mode_to_af_mode("AfModeEnum.Continuous") == 2

    def test_unknown_value_raises_value_error(self) -> None:
        """Unknown focus-mode strings raise so callers don't silently accept nonsense."""
        with pytest.raises(ValueError, match="Unsupported AfMode value"):
            _focus_mode_to_af_mode("warp-speed")


# ── _normalize_control_value ──────────────────────────────────────────────────


class TestNormalizeControlValue:
    """``_normalize_control_value`` coerces known friendly strings into enum values."""

    def test_afmode_string_coerced_to_enum(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A friendly AfMode string becomes the matching int enum."""
        monkeypatch.setattr(backend_mod, "controls", None)
        assert _normalize_control_value("AfMode", "continuous") == 2

    def test_afmode_non_string_passthrough(self) -> None:
        """A numeric AfMode value is already normalised and should pass through."""
        assert _normalize_control_value("AfMode", 1) == 1

    def test_unknown_control_passthrough(self) -> None:
        """Unknown control names should not be touched."""
        assert _normalize_control_value("ExposureTime", 10000) == 10000


# ── _control_options ──────────────────────────────────────────────────────────


class TestControlOptions:
    """``_control_options`` exposes enum options for Picamera2 controls with no min/max."""

    def test_afmode_options(self) -> None:
        """AfMode reports its three friendly names as options."""
        assert _control_options("AfMode") == ["manual", "auto", "continuous"]

    def test_unknown_control_returns_none(self) -> None:
        """Controls without a curated options list return ``None``."""
        assert _control_options("ExposureTime") is None


# ── _to_json_value + _serialize_mapping ──────────────────────────────────────


class _FakeEnum(Enum):
    NORMAL = "normal"
    WARM = "warm"


class TestToJsonValue:
    """The Picamera2 metadata value serialiser handles primitives, enums, lists, dicts, and unknowns."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            (True, True),
            (1, 1),
            (1.5, 1.5),
            ("hello", "hello"),
        ],
    )
    def test_primitive_values_pass_through(self, value: object, expected: object) -> None:
        """None and JSON primitives round-trip untouched."""
        assert _to_json_value(value) == expected

    def test_enum_value_serialized_by_name(self) -> None:
        """Anything with a ``.name`` attribute should be serialised as that name."""
        assert _to_json_value(_FakeEnum.WARM) == _FAKE_ENUM_WARM_NAME

    def test_list_values_recursed(self) -> None:
        """List elements are serialised recursively so nested enums render correctly."""
        assert _to_json_value([1, 2.0, "x", _FakeEnum.NORMAL]) == [1, 2.0, "x", _FAKE_ENUM_NORMAL_NAME]

    def test_tuple_values_recursed(self) -> None:
        """Tuples degrade to JSON lists."""
        assert _to_json_value((1, 2)) == [1, 2]

    def test_dict_values_recursed(self) -> None:
        """Dict values are serialised recursively."""
        assert _to_json_value({"a": 1, "b": _FakeEnum.WARM}) == {"a": 1, "b": _FAKE_ENUM_WARM_NAME}

    def test_unknown_types_fallback_to_str(self) -> None:
        """Anything without a JSON-compatible type falls back to ``str(value)``."""

        class _Opaque:
            def __str__(self) -> str:
                return _OPAQUE_REPR

        assert _to_json_value(_Opaque()) == _OPAQUE_REPR

    def test_serialize_mapping_round_trip(self) -> None:
        """``_serialize_mapping`` applies ``_to_json_value`` across the mapping values."""
        assert _serialize_mapping({"Thermal": _FakeEnum.WARM, "Exposure": 10000}) == {
            "Thermal": _FAKE_ENUM_WARM_NAME,
            "Exposure": 10000,
        }


# ── _value_type ───────────────────────────────────────────────────────────────


class TestValueType:
    """The value-type label helper used in camera controls capability reporting."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            (True, "boolean"),
            (1, "integer"),
            (1.5, "number"),
            ("hello", "string"),
            ([1, 2], "array"),
            ((1, 2), "array"),
        ],
    )
    def test_value_type_mapping(self, value: object, expected: str | None) -> None:
        """Primitive Python types map to the expected JSON-schema-ish labels."""
        assert _value_type(value) == expected

    def test_enum_value_reported_as_enum(self) -> None:
        """Values with a ``.name`` attribute report as ``"enum"``."""
        assert _value_type(_FakeEnum.NORMAL) == _ENUM_LABEL

    def test_unknown_type_returns_class_name(self) -> None:
        """Unknown class instances fall back to the class name."""

        class _Opaque:
            pass

        assert _value_type(_Opaque()) == _OPAQUE_CLASS_NAME


# ── Picamera2Backend._normalize_controls ─────────────────────────────────────


class TestNormalizeControls:
    """``Picamera2Backend._normalize_controls`` gates controls against camera_controls dict."""

    def test_unknown_controls_raise_value_error(self) -> None:
        """Controls absent from ``camera_controls`` raise a ``ValueError``."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"ExposureTime": (1, 1000, 100)}

        with pytest.raises(ValueError, match="Unknown camera controls: WarpDrive"):
            backend._normalize_controls(camera, {"WarpDrive": 42})

    def test_known_controls_pass_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Known controls are returned with their values normalised."""
        monkeypatch.setattr(backend_mod, "controls", None)
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"ExposureTime": (1, 1000, 100), "AfMode": (0, 2, 1)}

        result = backend._normalize_controls(camera, {"ExposureTime": 500, "AfMode": "auto"})

        assert result == {"ExposureTime": 500, "AfMode": 1}


# ── Picamera2Backend methods ─────────────────────────────────────────────────


class TestPicamera2BackendMethods:
    """Backend methods should behave correctly even with runtime stubs."""

    def test_require_camera_raises_when_uninitialized(self) -> None:
        """Accessing the camera before open should fail loudly."""
        backend = Picamera2Backend()
        with pytest.raises(RuntimeError, match=PICAMERA2_CAMERA_NOT_INITIALIZED):
            backend._require_camera()

    async def test_open_initializes_camera_and_enables_autofocus(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Open should initialize the camera once, configure video, and enable autofocus."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"AfMode": (0, 2, 1)}
        camera.create_video_configuration.return_value = {"config": "video"}
        monkeypatch.setattr(backend_mod, "Picamera2", MagicMock(return_value=camera))
        monkeypatch.setattr(backend_mod, "controls", None)

        await backend.open(CameraMode.VIDEO)

        camera.configure.assert_called_once_with({"config": "video"})
        camera.start.assert_called_once()
        camera.set_controls.assert_called_once_with({"AfMode": 2})
        assert backend.current_mode == CameraMode.VIDEO

    async def test_open_wraps_constructor_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Constructor failures should surface as CameraInitializationError."""
        monkeypatch.setattr(backend_mod, "Picamera2", MagicMock(side_effect=IndexError("missing")))
        backend = Picamera2Backend()

        with pytest.raises(CameraInitializationError, match=CAMERA_DEVICE_NOT_FOUND):
            await backend.open(CameraMode.VIDEO)

    async def test_capture_image_raises_when_metadata_missing(self) -> None:
        """Missing metadata should become a RuntimeError."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.capture_image.return_value = MagicMock()
        camera.capture_metadata.return_value = None
        camera.camera_properties = {}
        cast("Any", backend)._camera = camera

        with pytest.raises(RuntimeError, match="Failed to capture image metadata"):
            await backend.capture_image()

    async def test_start_stream_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A successful stream start should patch MediaMTX and start the encoder."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.start_encoder.return_value = None
        cast("Any", backend)._camera = camera
        mediamtx = cast("Any", backend._mediamtx)
        mediamtx.set_youtube_egress = AsyncMock()
        mediamtx.clear_egress = AsyncMock()
        monkeypatch.setattr(backend_mod, "H264Encoder", MagicMock(return_value=MagicMock(name="encoder")))
        monkeypatch.setattr(
            backend_mod,
            "build_hires_rtsp_output",
            MagicMock(return_value=MagicMock(name="output")),
        )
        config = YoutubeStreamConfig(stream_key=SecretStr("good"), broadcast_key=SecretStr("public-id"))

        result = await backend.start_stream(StreamMode.YOUTUBE, youtube_config=config)

        assert result.url == AnyUrl(YOUTUBE_PUBLIC_URL)
        mediamtx.set_youtube_egress.assert_awaited_once_with(PICAMERA2_CAM_HIRES_PATH, "good")
        camera.start_encoder.assert_called_once()
        assert camera.start_encoder.call_args.kwargs == {"name": PICAMERA2_MAIN_STREAM_NAME}

    async def test_start_stream_timeout_clears_egress(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A start timeout should clear egress and raise a runtime error."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.start_encoder.side_effect = TimeoutError
        cast("Any", backend)._camera = camera
        mediamtx = cast("Any", backend._mediamtx)
        mediamtx.set_youtube_egress = AsyncMock()
        mediamtx.clear_egress = AsyncMock()
        monkeypatch.setattr(backend_mod, "H264Encoder", MagicMock(return_value=MagicMock(name="encoder")))
        monkeypatch.setattr(
            backend_mod,
            "build_hires_rtsp_output",
            MagicMock(return_value=MagicMock(name="output")),
        )
        config = YoutubeStreamConfig(stream_key=SecretStr("good"), broadcast_key=SecretStr("public-id"))

        with pytest.raises(RuntimeError, match=PICAMERA2_STARTUP_TIMEOUT):
            await backend.start_stream(StreamMode.YOUTUBE, youtube_config=config)

        mediamtx.clear_egress.assert_awaited_once_with(PICAMERA2_CAM_HIRES_PATH)

    async def test_stop_stream_clears_egress_and_encoder(self) -> None:
        """Stopping should detach the encoder and clear the MediaMTX path."""
        backend = Picamera2Backend()
        camera = MagicMock()
        encoder = MagicMock()
        cast("Any", backend)._camera = camera
        cast("Any", backend)._main_encoder = encoder
        mediamtx = cast("Any", backend._mediamtx)
        mediamtx.clear_egress = AsyncMock()

        await backend.stop_stream()

        camera.stop_encoder.assert_called_once_with(encoder)
        mediamtx.clear_egress.assert_awaited_once_with(PICAMERA2_CAM_HIRES_PATH)
        assert backend._main_encoder is None

    async def test_get_stream_metadata_raises_when_missing(self) -> None:
        """Missing capture metadata should become a RuntimeError."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.capture_metadata.return_value = None
        cast("Any", backend)._camera = camera

        with pytest.raises(RuntimeError, match="Failed to capture image metadata"):
            await backend.get_stream_metadata()

    async def test_get_controls_and_capabilities_and_set_controls(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The controls flow should build a discoverable view and normalize control writes."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {
            "ExposureTime": (1, 1_000_000, 10_000),
            "AfMode": (0, 2, 1),
        }
        camera.capture_metadata.return_value = {"ExposureTime": 10_000}
        cast("Any", backend)._camera = camera
        monkeypatch.setattr(backend_mod, "controls", None)

        controls_view = await backend.get_controls()
        await backend.set_controls({"AfMode": "continuous"})

        assert controls_view.supported is True
        assert backend_mod._AF_MODE_CONTROL in controls_view.controls
        camera.set_controls.assert_called_with({backend_mod._AF_MODE_CONTROL: 2})

    async def test_set_focus_manual_rejects_missing_lens_control(self) -> None:
        """Manual focus with a lens position should fail if the control is unsupported."""
        backend = Picamera2Backend()
        camera = MagicMock()
        camera.camera_controls = {"AfMode": (0, 2, 1)}
        cast("Any", backend)._camera = camera

        with pytest.raises(ValueError, match="LensPosition"):
            await backend.set_focus(FocusControlRequest(mode=FocusMode.MANUAL, lens_position=2.5))

    async def test_cleanup_releases_camera(self) -> None:
        """Cleanup should stop and close the camera then clear internal state."""
        backend = Picamera2Backend()
        camera = MagicMock()
        cast("Any", backend)._camera = camera
        cast("Any", backend)._main_encoder = MagicMock()
        backend.current_mode = CameraMode.PHOTO

        await backend.cleanup()

        camera.stop.assert_called_once()
        camera.close.assert_called_once()
        assert backend._camera is None
        assert backend._main_encoder is None
        assert backend.current_mode is None
