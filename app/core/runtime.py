"""Application runtime container for long-lived process services."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import TYPE_CHECKING

from app.api.services.camera_manager import CameraManager
from app.api.services.hardware_protocols import Picamera2Like
from app.api.services.preview_pipeline import PreviewPipelineManager
from app.core.config import settings
from app.core.runtime_context import get_active_runtime, set_active_runtime
from app.core.runtime_state import RuntimeState
from app.utils.observability import ObservabilityHandle
from app.utils.pairing import PairingService
from app.utils.preview_sleeper import PreviewSleeper
from app.utils.relay import RelayService
from app.utils.relay_state import RelayRuntimeState
from app.utils.thermal_governor import ThermalGovernor

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
    thermal_governor: ThermalGovernor = field(init=False)
    background_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    recurring_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    observability_handle: ObservabilityHandle | None = None

    def __post_init__(self) -> None:
        self.relay_service = RelayService(state=self.relay_state, runtime_state=self.runtime_state)
        self.preview_sleeper = PreviewSleeper(
            pipeline=self.preview_pipeline,
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
        """Create, track, and auto-discard a managed background task."""
        task = asyncio.create_task(coro, name=name)
        self.track_task(task, recurring=recurring)
        return task

    def track_task(self, task: asyncio.Task[None], *, recurring: bool = False) -> asyncio.Task[None]:
        """Track an already-created task and discard it once complete."""
        task_set = self.recurring_tasks if recurring else self.background_tasks
        task_set.add(task)

        def _discard(done_task: asyncio.Task[None]) -> None:
            task_set.discard(done_task)

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

    async def wait_for_managed_tasks(self) -> None:
        """Wait for all currently tracked tasks to finish."""
        pending = tuple(self.background_tasks | self.recurring_tasks)
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)

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
