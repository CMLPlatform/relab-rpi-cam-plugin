"""Picamera2-backed camera implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, NoReturn, cast

from relab_rpi_cam_models.camera import CameraMode
from relab_rpi_cam_models.stream import StreamMode

from app.api.exceptions import CameraInitializationError
from app.api.schemas.camera_controls import (
    CameraControlInfo,
    CameraControlsCapabilities,
    CameraControlsView,
    FocusControlRequest,
    FocusMode,
    JsonValue,
)
from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.camera_backend import (
    CaptureResult,
    ControllableCameraBackend,
    StreamingCameraBackend,
    StreamStartResult,
)
from app.api.services.hardware_protocols import Picamera2Like
from app.api.services.hardware_stubs import H264EncoderStub, Picamera2Stub
from app.api.services.stream import get_broadcast_url, get_ffmpeg_output
from app.core.config import settings

if TYPE_CHECKING:
    # libcamera's `controls` module isn't available to the typechecker
    # in all environments; treat it as Any for static checks.
    controls: Any
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder
else:
    try:
        from picamera2 import Picamera2
        from picamera2.encoders import H264Encoder
    except ImportError:
        Picamera2 = Picamera2Stub
        H264Encoder = H264EncoderStub
    try:
        from libcamera import controls
    except ImportError:
        controls = None

_AF_MODE_CONTROL = "AfMode"
_LENS_POSITION_CONTROL = "LensPosition"

logger = logging.getLogger(__name__)

# Main stream: full-resolution buffer used for stills and, when active, the
# YouTube H264 encoder. Lores stream: low-resolution buffer reserved for the
# preview H264 encoder (MediaMTX WHEP, Phase 6) — much cheaper on CPU than
# encoding main, which matters because preview is the dominant use case while
# YouTube streaming is rare.
_MAIN_SIZE = (1920, 1080)
_LORES_SIZE = (640, 480)


class Picamera2Backend(StreamingCameraBackend, ControllableCameraBackend):
    """Concrete camera backend backed by Picamera2.

    Runs a single persistent video configuration with both a main (1080p) and a
    lores (640x480) stream. Stills are pulled from the running main stream via
    ``capture_image("main")`` — Pi 5's dual-ISP handles still-while-recording,
    so no mode switching or pipeline restart is needed.
    """

    def __init__(self) -> None:
        self._camera: Picamera2Like | None = None
        self._main_encoder: H264Encoder | None = None
        self.current_mode: CameraMode | None = None

    @property
    def camera(self) -> Picamera2Like | None:
        """The live Picamera2 instance, or ``None`` if not yet opened."""
        return self._camera

    async def open(self, mode: CameraMode) -> None:
        """Initialise the persistent pipeline on first call; idempotent thereafter."""
        if self._camera is None:
            try:
                self._camera = cast(
                    "Picamera2Like", await asyncio.to_thread(lambda: Picamera2(camera_num=settings.camera_device_num))
                )
            except IndexError as e:
                raise CameraInitializationError(
                    settings.camera_device_num,
                    "Camera device not found. Check that the device number is correct and the camera is connected.",
                ) from e
            except (RuntimeError, OSError) as e:
                raise CameraInitializationError(settings.camera_device_num, str(e)) from e

            camera = self._require_camera()
            # RGB888 on main (24bpp) cuts per-buffer DMA by 25% vs picamera2's
            # default XBGR8888 (32bpp) and matches what ``capture_image`` ends up
            # converting to anyway — no extra colour-space hop. ``buffer_count=4``
            # trims the default ``create_video_configuration`` value of 6 so the
            # dual main+lores pipeline fits comfortably inside the Pi's default
            # CMA reservation (previously seen as dma_heap ENOMEM at configure
            # time). ``display=None`` skips an unused preview pathway.
            config = camera.create_video_configuration(
                main={"size": _MAIN_SIZE, "format": "RGB888"},
                lores={"size": _LORES_SIZE, "format": "YUV420"},
                buffer_count=4,
                display=None,
                raw=None,
            )
            camera.configure(config)
            await asyncio.to_thread(camera.start)
            self._enable_default_autofocus(camera)

        self.current_mode = mode

    async def capture_image(self) -> CaptureResult:
        """Capture a still from the running main stream."""
        await self.open(CameraMode.PHOTO)
        camera = self._require_camera()
        image = await asyncio.to_thread(camera.capture_image, "main")
        capture_metadata = await asyncio.wait_for(asyncio.to_thread(camera.capture_metadata), timeout=10)
        if capture_metadata is None:
            msg = "Failed to capture image metadata"
            raise RuntimeError(msg)
        return CaptureResult(
            image=image,
            camera_properties=camera.camera_properties,
            capture_metadata=capture_metadata,
        )

    async def start_stream(
        self,
        mode: StreamMode,
        *,
        youtube_config: YoutubeStreamConfig | None = None,
    ) -> StreamStartResult:
        """Start a provider-backed stream on the main H264 encoder.

        Uses ``start_encoder(name="main")`` instead of ``start_recording`` so the
        persistent picamera2 pipeline (set up in ``open()`` during Phase 1)
        stays up — stills-while-streaming keeps working because the main stream
        continues feeding frames to ``capture_image`` simultaneously.
        """
        if mode == StreamMode.YOUTUBE and not youtube_config:
            raise YoutubeConfigRequiredError

        await self.open(CameraMode.VIDEO)
        camera = self._require_camera()

        try:
            stream_output = get_ffmpeg_output(mode, youtube_config)
            encoder = H264Encoder()
            await asyncio.wait_for(
                asyncio.to_thread(camera.start_encoder, encoder, stream_output, name="main"),
                timeout=30.0,
            )
            self._main_encoder = encoder
        except TimeoutError as e:
            msg = "Failed to start recording: ffmpeg startup timeout"
            raise RuntimeError(msg) from e
        except (OSError, RuntimeError) as e:
            msg = f"Failed to start recording: {e}"
            raise RuntimeError(msg) from e

        url = get_broadcast_url(youtube_config) if youtube_config else None
        if url is None:
            if self._main_encoder is not None:
                await asyncio.to_thread(camera.stop_encoder, self._main_encoder)
                self._main_encoder = None
            _raise_missing_stream_url()

        return StreamStartResult(mode=mode, url=url)

    async def stop_stream(self) -> None:
        """Stop the main encoder without touching the rest of the persistent pipeline."""
        camera = self._require_camera()
        if self._main_encoder is None:
            return
        await asyncio.to_thread(camera.stop_encoder, self._main_encoder)
        self._main_encoder = None

    async def get_stream_metadata(self) -> tuple[dict, dict]:
        """Return metadata for the active stream."""
        camera = self._require_camera()
        capture_metadata = await asyncio.wait_for(asyncio.to_thread(camera.capture_metadata), timeout=10)
        if capture_metadata is None:
            msg = "Failed to capture image metadata"
            raise RuntimeError(msg)
        return camera.camera_properties, capture_metadata

    async def get_controls(self) -> CameraControlsView:
        """Return Picamera2 controls and latest observed metadata values."""
        await self.open(CameraMode.VIDEO)
        camera = self._require_camera()
        return await self._build_controls_view(camera)

    async def get_controls_capabilities(self) -> CameraControlsCapabilities:
        """Return a UI-friendly list of Picamera2 controls."""
        await self.open(CameraMode.VIDEO)
        camera = self._require_camera()
        view = await self._build_controls_view(camera)
        return CameraControlsCapabilities(
            supported=True,
            controls=sorted(view.controls.values(), key=lambda item: item.name.lower()),
        )

    async def set_controls(self, controls: dict[str, JsonValue]) -> CameraControlsView:
        """Apply Picamera2 controls by backend-native control name."""
        await self.open(CameraMode.VIDEO)
        camera = self._require_camera()
        normalized_controls = self._normalize_controls(camera, controls)
        await asyncio.to_thread(camera.set_controls, normalized_controls)
        return await self._build_controls_view(camera)

    async def set_focus(self, request: FocusControlRequest) -> CameraControlsView:
        """Apply a friendly focus request."""
        await self.open(CameraMode.VIDEO)
        camera = self._require_camera()
        available = camera.camera_controls

        if _AF_MODE_CONTROL not in available:
            msg = "Camera does not support autofocus controls"
            raise ValueError(msg)

        if request.mode == FocusMode.CONTINUOUS:
            await asyncio.to_thread(camera.set_controls, {_AF_MODE_CONTROL: _af_mode_continuous()})
        elif request.mode == FocusMode.AUTO:
            if request.trigger_cycle:
                await asyncio.to_thread(camera.autofocus_cycle, wait=True)
            else:
                await asyncio.to_thread(camera.set_controls, {_AF_MODE_CONTROL: _af_mode_auto()})
        elif request.mode == FocusMode.MANUAL:
            control_values: dict[str, object] = {_AF_MODE_CONTROL: _af_mode_manual()}
            if request.lens_position is not None:
                if _LENS_POSITION_CONTROL not in available:
                    msg = "Camera does not support LensPosition control"
                    raise ValueError(msg)
                control_values[_LENS_POSITION_CONTROL] = request.lens_position
            await asyncio.to_thread(camera.set_controls, control_values)
        else:
            msg = f"Unsupported focus mode: {request.mode}"
            raise ValueError(msg)

        return await self._build_controls_view(camera)

    async def cleanup(self) -> None:
        """Release Picamera2 resources."""
        if self._camera:
            await asyncio.to_thread(self._camera.stop)
            await asyncio.to_thread(self._camera.close)
            self._camera = None
            self._main_encoder = None
            self.current_mode = None

    def _require_camera(self) -> Picamera2Like:
        """Return the initialized camera or raise a runtime error."""
        if self._camera is None:
            msg = "Camera backend has not been initialized"
            raise RuntimeError(msg)
        return self._camera

    def _enable_default_autofocus(self, camera: Picamera2Like) -> None:
        """Enable continuous autofocus when the active camera exposes AfMode."""
        if _AF_MODE_CONTROL in camera.camera_controls:
            camera.set_controls({_AF_MODE_CONTROL: _af_mode_continuous()})

    async def _build_controls_view(self, camera: Picamera2Like) -> CameraControlsView:
        """Build a serializable controls view from Picamera2 state."""
        capture_metadata = await asyncio.to_thread(camera.capture_metadata)
        values = _serialize_mapping(capture_metadata or {})
        return CameraControlsView(
            supported=True,
            controls={
                name: CameraControlInfo(
                    name=name,
                    namespace="picamera2",
                    value_type=_value_type(default if default is not None else minimum),
                    minimum=_to_json_value(minimum),
                    maximum=_to_json_value(maximum),
                    default=_to_json_value(default),
                    options=_control_options(name),
                )
                for name, (minimum, maximum, default) in camera.camera_controls.items()
            },
            values=values,
        )

    def _normalize_controls(self, camera: Picamera2Like, controls_patch: dict[str, JsonValue]) -> dict[str, object]:
        """Validate backend-native control names and coerce known friendly enum strings."""
        available = camera.camera_controls
        unknown = sorted(set(controls_patch) - set(available))
        if unknown:
            msg = f"Unknown camera controls: {', '.join(unknown)}"
            raise ValueError(msg)

        return {name: _normalize_control_value(name, value) for name, value in controls_patch.items()}


def _raise_missing_stream_url() -> NoReturn:
    """Raise the standard error for backends that fail to expose a stream URL."""
    msg = "Streaming backend did not return a public stream URL"
    raise RuntimeError(msg)


def _af_mode_manual() -> object:
    """Return the libcamera manual autofocus enum."""
    return controls.AfModeEnum.Manual if controls else 0


def _af_mode_auto() -> object:
    """Return the libcamera single-shot autofocus enum."""
    return controls.AfModeEnum.Auto if controls else 1


def _af_mode_continuous() -> object:
    """Return the libcamera continuous autofocus enum."""
    return controls.AfModeEnum.Continuous if controls else 2


def _normalize_control_value(name: str, value: JsonValue) -> object:
    """Coerce known control values from API JSON into Picamera2/libcamera values."""
    if name == _AF_MODE_CONTROL and isinstance(value, str):
        return _focus_mode_to_af_mode(value)
    return value


def _focus_mode_to_af_mode(value: str) -> object:
    """Map a friendly or libcamera-style focus mode string to AfModeEnum."""
    normalized = value.removeprefix("AfModeEnum.").lower()
    match normalized:
        case "manual":
            return _af_mode_manual()
        case "auto":
            return _af_mode_auto()
        case "continuous":
            return _af_mode_continuous()
        case _:
            msg = f"Unsupported AfMode value: {value}"
            raise ValueError(msg)


def _control_options(name: str) -> list[JsonValue] | None:
    """Return known enum options for controls where Picamera2 reports only min/max/default."""
    if name == _AF_MODE_CONTROL:
        return ["manual", "auto", "continuous"]
    return None


def _serialize_mapping(values: dict[str, object]) -> dict[str, JsonValue]:
    """Serialize Picamera2 metadata values for API responses."""
    return {name: _to_json_value(value) for name, value in values.items()}


def _to_json_value(value: object) -> JsonValue:
    """Convert common Picamera2/libcamera values to JSON-compatible values."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if hasattr(value, "name"):
        return str(value.name)
    if isinstance(value, list | tuple):
        return [_to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    return str(value)


def _value_type(value: object) -> str | None:
    """Return a compact value type label for a control."""
    if value is None:
        return None
    if hasattr(value, "name"):
        return "enum"
    value_type = type(value).__name__
    match value:
        case bool():
            value_type = "boolean"
        case int():
            value_type = "integer"
        case float():
            value_type = "number"
        case str():
            value_type = "string"
        case list() | tuple():
            value_type = "array"
        case _:
            value_type = type(value).__name__
    return value_type
