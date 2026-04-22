"""Tests for application runtime container helpers."""

import asyncio
import logging

import pytest
from fastapi import FastAPI

from app.core.runtime import AppRuntime, ensure_app_runtime

BACKGROUND_TASK_NAME = "bg-task"
FAILING_TASK_NAME = "failing-task"
FAILING_TASK_LOG = f"Managed task '{FAILING_TASK_NAME}' failed"


class TestAppRuntime:
    """Tests for runtime-managed tasks and state attachment."""

    async def test_create_task_tracks_and_discards_completed_background_task(self) -> None:
        """Tracked background tasks should disappear after completion."""
        runtime = AppRuntime()

        async def _work() -> None:
            await asyncio.sleep(0)

        task = runtime.create_task(_work(), name=BACKGROUND_TASK_NAME)
        assert task in runtime.background_tasks
        discarded = asyncio.Event()
        task.add_done_callback(lambda _task: discarded.set())

        await asyncio.wait_for(task, timeout=1)
        await asyncio.wait_for(discarded.wait(), timeout=1)

        assert task not in runtime.background_tasks
        assert BACKGROUND_TASK_NAME not in runtime.managed_tasks_by_name

    async def test_create_task_replaces_running_task_with_same_name(self) -> None:
        """Starting the same managed task twice should cancel the older loop."""
        runtime = AppRuntime()
        first_cancelled = asyncio.Event()
        second_started = asyncio.Event()

        async def _first() -> None:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                first_cancelled.set()
                raise

        async def _second() -> None:
            second_started.set()
            await asyncio.Future()

        first_task = runtime.create_task(_first(), name="shared")
        await asyncio.sleep(0)
        second_task = runtime.create_task(_second(), name="shared")

        await asyncio.wait_for(first_cancelled.wait(), timeout=1)
        await asyncio.wait_for(second_started.wait(), timeout=1)

        assert runtime.managed_tasks_by_name["shared"] is second_task
        assert first_task.cancelled()

        await runtime.stop_tasks({"shared"})

    async def test_stop_tasks_waits_for_targeted_tasks_only(self) -> None:
        """Stopping named tasks should leave unrelated tasks alone."""
        runtime = AppRuntime()
        keep_running = asyncio.Event()

        async def _worker(stop_event: asyncio.Event) -> None:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                stop_event.set()
                raise

        stopped = asyncio.Event()
        target = runtime.create_task(_worker(stopped), name="target")
        other = runtime.create_task(_worker(keep_running), name="other")

        await runtime.stop_tasks({"target"})

        assert target.cancelled()
        assert not other.cancelled()
        assert runtime.managed_tasks_by_name["other"] is other

        await runtime.stop_tasks({"other"})

    async def test_track_task_logs_unexpected_failure_once(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unexpected managed task failures should be logged with their task name."""
        runtime = AppRuntime()

        async def _boom() -> None:
            err_msg = "boom"
            raise RuntimeError(err_msg)

        with caplog.at_level(logging.ERROR):
            task = runtime.create_task(_boom(), name=FAILING_TASK_NAME)
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

        assert FAILING_TASK_LOG in caplog.text

    def test_ensure_app_runtime_reuses_existing_runtime(self) -> None:
        """Attaching runtime twice to one app should reuse the existing instance."""
        app = FastAPI()

        first = ensure_app_runtime(app)
        second = ensure_app_runtime(app)

        assert first is second
