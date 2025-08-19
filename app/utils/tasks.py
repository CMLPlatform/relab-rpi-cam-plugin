"""Simple task repetition utilities."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

repeated_tasks = set()


def repeat_task(
    task_func: Callable[[], None] | Callable[[], Coroutine[Any, Any, None]],
    seconds: float,
    task_name: str,
) -> asyncio.Task:
    """Start a task that repeats every x seconds in the background."""

    async def _loop() -> None:
        """Internal loop function."""
        while True:
            try:
                if asyncio.iscoroutinefunction(task_func):
                    await task_func()
                else:
                    await run_in_threadpool(task_func)
                logger.info("Task '%s' executed successfully", task_name)
            except Exception:
                logger.exception("Exception in task '%s'", task_name)

            await asyncio.sleep(seconds)

    # Store tasks in a global set to avoid accidental garbage collection
    task = asyncio.ensure_future(_loop())
    # repeated_tasks.add(task)
    return task
