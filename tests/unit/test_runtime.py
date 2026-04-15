"""Tests for application runtime container helpers."""

import asyncio

from fastapi import FastAPI

from app.core.runtime import AppRuntime, ensure_app_runtime


class TestAppRuntime:
    """Tests for runtime-managed tasks and state attachment."""

    async def test_create_task_tracks_and_discards_completed_background_task(self) -> None:
        """Tracked background tasks should disappear after completion."""
        runtime = AppRuntime()

        async def _work() -> None:
            await asyncio.sleep(0)

        task = runtime.create_task(_work(), name="bg-task")
        assert task in runtime.background_tasks

        await task
        await asyncio.sleep(0)

        assert task not in runtime.background_tasks

    def test_ensure_app_runtime_reuses_existing_runtime(self) -> None:
        """Attaching runtime twice to one app should reuse the existing instance."""
        app = FastAPI()

        first = ensure_app_runtime(app)
        second = ensure_app_runtime(app)

        assert first is second
