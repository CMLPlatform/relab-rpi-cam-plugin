"""Typed test doubles for app/runtime-oriented tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from PIL import Image
from pydantic import AnyUrl
from relab_rpi_cam_models.camera import CameraMode

from app.camera.schemas import (
    CameraControlInfo,
    CameraControlsView,
    FocusControlRequest,
    JsonValue,
    YoutubeStreamConfig,
)
from app.camera.services.backend import CaptureResult, StreamingCameraBackend, StreamStartResult
from app.camera.services.manager import CameraManager
from app.core.runtime import AppRuntime
from app.delivery.base import ImageSink, StoredImage
from app.pairing.services.service import PairingService, PairingState
from app.relay.service import RelayService
from app.relay.state import RelayRuntimeState
from app.workers.preview_sleeper import PreviewSleeper
from app.workers.preview_thumbnail import PreviewThumbnailWorker
from app.workers.thermal_governor import ThermalGovernor
from tests.constants import YOUTUBE_TEST_BROADCAST_URL

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping

    from relab_rpi_cam_models.stream import StreamMode

    from app.camera.services.backend import CameraBackend


class FakeBackend:
    """Provider-neutral camera backend for test usage."""

    def __init__(self) -> None:
        self.current_mode: CameraMode | None = None
        self.cleaned_up = False
        self.stream_active = False
        self.stream_url = AnyUrl(YOUTUBE_TEST_BROADCAST_URL)
        self.image = Image.new("RGB", (100, 100), color="red")
        self.camera_properties = {"Model": "mock-camera"}
        self.capture_metadata: dict[str, JsonValue] = {"FrameDuration": 33333}
        self.last_youtube_config: YoutubeStreamConfig | None = None
        self.controls: dict[str, JsonValue] = {}

    @property
    def is_open(self) -> bool:
        """Return True when the fake backend has been opened."""
        return self.current_mode is not None

    async def start_lores_encoder(self, encoder: object, output: object) -> None:
        """No-op lores encoder start for the fake backend."""

    async def stop_lores_encoder(self, encoder: object) -> None:
        """No-op lores encoder stop for the fake backend."""

    async def open(self, mode: CameraMode) -> None:
        """Open the fake backend."""
        self.current_mode = mode

    async def capture_image(self) -> CaptureResult:
        """Capture a fake still image."""
        self.current_mode = CameraMode.PHOTO
        return CaptureResult(
            image=self.image,
            camera_properties=self.camera_properties,
            capture_metadata=self.capture_metadata,
        )

    async def capture_preview_frame(self) -> Image.Image:
        """Return a low-cost preview frame from the fake backend."""
        return self.image

    async def start_stream(
        self,
        mode: StreamMode,
        *,
        youtube_config: YoutubeStreamConfig | None = None,
    ) -> StreamStartResult:
        """Start a fake stream."""
        self.current_mode = CameraMode.VIDEO
        self.stream_active = True
        self.last_youtube_config = youtube_config
        return StreamStartResult(mode=mode, url=self.stream_url)

    async def stop_stream(self) -> None:
        """Stop a fake stream."""
        self.stream_active = False

    async def get_stream_metadata(self) -> tuple[dict[str, str], dict[str, JsonValue]]:
        """Return mock camera metadata."""
        return self.camera_properties, self.capture_metadata

    async def get_controls(self) -> CameraControlsView:
        """Return mock control capabilities."""
        return CameraControlsView(
            supported=True,
            controls={
                "AfMode": CameraControlInfo(
                    name="AfMode",
                    namespace="fake",
                    value_type="enum",
                    options=["manual", "auto", "continuous"],
                ),
            },
            values=self.capture_metadata,
        )

    async def set_controls(self, controls: dict[str, JsonValue]) -> CameraControlsView:
        """Store and return mock controls."""
        self.controls.update(controls)
        return await self.get_controls()

    async def set_focus(self, request: FocusControlRequest) -> CameraControlsView:
        """Store and return mock focus controls."""
        self.controls["FocusMode"] = request.mode
        if request.lens_position is not None:
            self.controls["LensPosition"] = request.lens_position
        return await self.get_controls()

    async def cleanup(self) -> None:
        """Release mock resources."""
        self.cleaned_up = True
        self.current_mode = None


@dataclass
class FakeRelayService(RelayService):
    """Typed relay service double for runtime-oriented tests."""

    run_calls: int = 0

    def __init__(self, runtime: AppRuntime) -> None:
        super().__init__(state=runtime.relay_state, runtime_state=runtime.runtime_state)
        self.run_calls = 0

    async def run_forever(self) -> None:
        """Record that the relay loop was requested."""
        self.run_calls += 1


@dataclass
class FakePairingService(PairingService):
    """Typed pairing service double for runtime-oriented tests."""

    state: PairingState = field(default_factory=PairingState)
    run_calls: int = 0
    reset_calls: int = 0
    log_calls: int = 0
    auto_pair: bool = True

    def __init__(self, *, auto_pair: bool = True) -> None:
        super().__init__()
        self.run_calls = 0
        self.reset_calls = 0
        self.log_calls = 0
        self.auto_pair = auto_pair

    def get_state(self) -> PairingState:
        """Return the current pairing state."""
        return self.state

    def reset_state(self) -> None:
        """Reset the pairing state to idle."""
        self.reset_calls += 1
        self.state = PairingState()

    def log_mode_started(self) -> None:
        """Record pairing-mode banner emission."""
        self.log_calls += 1

    async def run_forever(self, on_paired: Callable[[], Awaitable[None]]) -> None:
        """Record that pairing was started and optionally complete immediately."""
        self.run_calls += 1
        if self.auto_pair:
            await on_paired()


class StubCameraManager(CameraManager):
    """Camera manager stub for lifespan tests; records cleanup force flags."""

    def __init__(self) -> None:
        super().__init__(backend=cast("StreamingCameraBackend", FakeBackend()))
        self.cleanup_calls: list[bool] = []

    async def setup_camera(self, mode: CameraMode) -> None:
        """No-op setup; avoids touching hardware during lifespan tests."""
        del mode

    async def cleanup(self, *, force: bool = False) -> None:
        """Record cleanup requests without touching hardware."""
        self.cleanup_calls.append(force)

    async def get_stream_info(self) -> None:
        """Return no active stream metadata in lifespan-oriented tests."""
        return

    async def stop_streaming(self) -> None:
        """Clear stream mode without touching hardware."""
        self.stream.mode = None


class FakePreviewSleeper(PreviewSleeper):
    """Preview sleeper double that records lifecycle calls."""

    def __init__(self, runtime: AppRuntime) -> None:
        super().__init__(
            pipeline=runtime.preview_pipeline,
            relay_state=runtime.relay_state,
            relay_enabled_getter=lambda: runtime.runtime_state.relay_enabled,
        )
        self.configure_calls = 0
        self.run_calls = 0

    def configure(self, *, backend: CameraBackend) -> None:
        """Record sleeper configuration without spawning background work."""
        del backend
        self.configure_calls += 1

    async def run_forever(self) -> None:
        """Record sleeper loop startup and then idle until cancelled."""
        self.run_calls += 1
        await cast("asyncio.Future[None]", asyncio.Future())


class FakeThermalGovernor(ThermalGovernor):
    """Thermal-governor double that records lifecycle calls."""

    def __init__(self, runtime: AppRuntime) -> None:
        super().__init__(runtime.preview_pipeline)
        self.configure_calls = 0
        self.run_calls = 0

    def configure(self, *, backend: CameraBackend) -> None:
        """Record governor configuration without spawning background work."""
        del backend
        self.configure_calls += 1

    async def run_forever(self) -> None:
        """Record governor loop startup and then idle until cancelled."""
        self.run_calls += 1
        await cast("asyncio.Future[None]", asyncio.Future())


class FakePreviewThumbnailWorker(PreviewThumbnailWorker):
    """Preview-thumbnail worker double that records runtime-managed lifecycle."""

    def __init__(self) -> None:
        super().__init__(
            camera_manager=make_camera_manager(),
            relay_state=RelayRuntimeState(),
            relay_enabled_getter=lambda: False,
        )
        self.run_calls = 0

    async def run_forever(self) -> None:
        """Record worker startup and then idle until cancelled."""
        self.run_calls += 1
        await cast("asyncio.Future[None]", asyncio.Future())


class SpyRuntime(AppRuntime):
    """Runtime that records managed-task activity for router tests."""

    def __init__(self, *, camera_manager: CameraManager | None = None) -> None:
        super().__init__(camera_manager=camera_manager or make_camera_manager())
        self.created_tasks: list[asyncio.Task[None]] = []
        self.cancelled_task_names: list[set[str] | None] = []

    def create_task(
        self,
        coro: Coroutine[object, object, None],
        *,
        name: str,
        recurring: bool = False,
    ) -> asyncio.Task[None]:
        """Track created tasks while preserving AppRuntime behavior."""
        task = super().create_task(coro, name=name, recurring=recurring)
        self.created_tasks.append(task)
        return task

    def cancel_tasks(self, names: set[str] | None = None) -> None:
        """Record cancellations while preserving AppRuntime behavior."""
        self.cancelled_task_names.append(names)
        super().cancel_tasks(names)


def make_camera_manager() -> CameraManager:
    """Create a camera manager backed by the typed fake backend."""
    return CameraManager(backend=cast("StreamingCameraBackend", FakeBackend()))


class _NoopImageSink(ImageSink):
    async def put(
        self,
        *,
        image_id: str,
        image_bytes: bytes,
        filename: str,
        capture_metadata: Mapping[str, object],
        upload_metadata: Mapping[str, object],
    ) -> StoredImage:
        del image_bytes, filename, capture_metadata, upload_metadata
        return StoredImage(image_id=image_id, image_url=AnyUrl("https://example.invalid/noop-image.jpg"))


def fresh_p256_pem() -> str:
    """Mint a throwaway P-256 private key in PEM form."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def build_test_runtime(*, camera_manager: CameraManager | None = None) -> AppRuntime:
    """Create an app runtime with test-oriented fake services."""
    runtime = AppRuntime(camera_manager=camera_manager or make_camera_manager())
    runtime.relay_service = FakeRelayService(runtime)
    runtime.pairing_service = FakePairingService()
    return runtime
