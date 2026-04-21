"""Application runtime container for long-lived process services."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import TYPE_CHECKING

from app.camera.services.hardware_protocols import Picamera2Like
from app.camera.services.manager import CameraManager
from app.core.runtime_context import get_active_runtime, set_active_runtime
from app.core.runtime_state import RuntimeState
from app.core.settings import settings
from app.media.preview_pipeline import PreviewPipelineManager
from app.observability.tracing import ObservabilityHandle
from app.pairing.services.service import PairingService
from app.relay.service import RelayService
from app.relay.state import RelayRuntimeState
from app.upload.queue import UploadQueueWorker
from app.workers.preview_sleeper import PreviewSleeper
from app.workers.preview_thumbnail import PreviewThumbnailWorker
from app.workers.thermal_governor import ThermalGovernor

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)
__all__ = [
    "AppRuntime",
    "ensure_app_runtime",
    "get_active_runtime",
    "get_app_runtime",
    "get_request_runtime",
    "set_active_runtime",
]


@dataclass
class AppRuntime:
    """Own the process-wide runtime services and managed background tasks."""

    camera_manager: CameraManager = field(default_factory=CameraManager)
    runtime_state: RuntimeState = field(default_factory=lambda: RuntimeState.from_settings(settings))
    preview_pipeline: PreviewPipelineManager = field(default_factory=PreviewPipelineManager)
    relay_state: RelayRuntimeState = field(default_factory=RelayRuntimeState)
    pairing_service: PairingService = field(default_factory=PairingService)
    relay_service: RelayService = field(init=False)
    preview_sleeper: PreviewSleeper = field(init=False)
    preview_thumbnail_worker: PreviewThumbnailWorker = field(init=False)
    thermal_governor: ThermalGovernor = field(init=False)
    upload_queue_worker: UploadQueueWorker | None = None
    background_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    recurring_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    managed_tasks_by_name: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    observability_handle: ObservabilityHandle | None = None

    def __post_init__(self) -> None:
        self.relay_service = RelayService(state=self.relay_state, runtime_state=self.runtime_state)
        self.preview_sleeper = PreviewSleeper(
            pipeline=self.preview_pipeline,
            relay_state=self.relay_state,
            relay_enabled_getter=lambda: self.runtime_state.relay_enabled,
        )
        self.preview_thumbnail_worker = PreviewThumbnailWorker(
            camera_manager=self.camera_manager,
            relay_state=self.relay_state,
            relay_enabled_getter=lambda: self.runtime_state.relay_enabled,
        )
        self.thermal_governor = ThermalGovernor(self.preview_pipeline)

    def create_task(
        self,
        coro: Coroutine[object, object, None],
        *,
        name: str,
        recurring: bool = False,
    ) -> asyncio.Task[None]:
        """Create, track, and auto-discard a managed background task.

        Starting a managed task with an existing name deterministically cancels
        the older task before registering the replacement.
        """
        existing = self.managed_tasks_by_name.get(name)
        if existing is not None and not existing.done():
            existing.cancel()
        task = asyncio.create_task(coro, name=name)
        self.track_task(task, recurring=recurring)
        return task

    def track_task(self, task: asyncio.Task[None], *, recurring: bool = False) -> asyncio.Task[None]:
        """Track an already-created task and discard it once complete."""
        task_set = self.recurring_tasks if recurring else self.background_tasks
        task_set.add(task)
        self.managed_tasks_by_name[task.get_name()] = task

        def _discard(done_task: asyncio.Task[None]) -> None:
            if not done_task.cancelled():
                exc = done_task.exception()
                if exc is not None:
                    logger.exception("Managed task '%s' failed", done_task.get_name(), exc_info=exc)
            task_set.discard(done_task)
            if self.managed_tasks_by_name.get(done_task.get_name()) is done_task:
                del self.managed_tasks_by_name[done_task.get_name()]

        task.add_done_callback(_discard)
        return task

    def create_repeating_task(
        self,
        task_func: Callable[[], None] | Callable[[], Awaitable[None]],
        *,
        seconds: float,
        name: str,
    ) -> asyncio.Task[None]:
        """Create a tracked repeating task."""

        async def _loop() -> None:
            while True:
                try:
                    result = task_func()
                    if isawaitable(result):
                        await result
                except Exception:
                    logger.exception("Exception in task '%s'", name)
                await asyncio.sleep(seconds)

        return self.create_task(_loop(), name=name, recurring=True)

    def cancel_tasks(self, names: set[str] | None = None) -> None:
        """Cancel tracked tasks, optionally filtered by task name."""
        for task in self.background_tasks | self.recurring_tasks:
            if names is not None and task.get_name() not in names:
                continue
            task.cancel()

    async def wait_for_tasks(self, names: set[str] | None = None) -> None:
        """Wait for tracked tasks, optionally filtered by task name."""
        pending = tuple(
            task for task in self.background_tasks | self.recurring_tasks if names is None or task.get_name() in names
        )
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)

    async def stop_tasks(self, names: set[str]) -> None:
        """Cancel and wait for the named managed tasks to finish."""
        self.cancel_tasks(names)
        await self.wait_for_tasks(names)

    async def wait_for_managed_tasks(self) -> None:
        """Wait for all currently tracked tasks to finish."""
        await self.wait_for_tasks()

    def start_preview_sleeper(self) -> asyncio.Task[None]:
        """Start or replace the preview sleeper loop under runtime ownership."""
        self.preview_sleeper.configure(camera_getter=self.camera_getter)
        return self.create_task(self.preview_sleeper.run_forever(), name="preview_sleeper")

    def start_thermal_governor(self) -> asyncio.Task[None]:
        """Start or replace the thermal governor loop under runtime ownership."""
        self.thermal_governor.configure(camera_getter=self.camera_getter)
        return self.create_task(self.thermal_governor.run_forever(), name="thermal_governor")

    def start_preview_thumbnail_worker(self) -> asyncio.Task[None]:
        """Start or replace the cached preview-thumbnail worker under runtime ownership."""
        return self.create_task(self.preview_thumbnail_worker.run_forever(), name="preview_thumbnail_worker")

    def start_upload_queue_worker(self) -> asyncio.Task[None]:
        """Start or replace the upload queue worker under runtime ownership."""
        if self.upload_queue_worker is None:
            self.upload_queue_worker = UploadQueueWorker(self.camera_manager.upload_queue)
        return self.create_task(self.upload_queue_worker.run_forever(), name="upload_queue_worker")

    async def stop_runtime_workers(self) -> None:
        """Stop runtime-owned long-lived worker loops in dependency order."""
        await self.stop_tasks(
            {
                "preview_sleeper",
                "preview_thumbnail_worker",
                "thermal_governor",
                "upload_queue_worker",
            }
        )

    def camera_getter(self) -> Picamera2Like | None:
        """Return the live backend camera object for background services."""
        return self.camera_manager.backend.camera


def ensure_app_runtime(app: FastAPI) -> AppRuntime:
    """Attach and return the runtime container for the given app."""
    runtime = getattr(app.state, "runtime", None)
    if isinstance(runtime, AppRuntime):
        set_active_runtime(runtime)
        return runtime
    runtime = AppRuntime()
    app.state.runtime = runtime
    set_active_runtime(runtime)
    return runtime


def get_app_runtime(app: FastAPI) -> AppRuntime:
    """Return the app runtime, creating it lazily if needed."""
    return ensure_app_runtime(app)


def get_request_runtime(request: Request) -> AppRuntime:
    """Return the runtime bound to the request's app."""
    return ensure_app_runtime(request.app)
