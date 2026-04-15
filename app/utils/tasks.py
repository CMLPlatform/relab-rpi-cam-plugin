"""Simple task repetition utilities."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)


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
                    await asyncio.to_thread(task_func)
            except Exception:
                logger.exception("Exception in task '%s'", task_name)

            await asyncio.sleep(seconds)

    # Create and return task; caller is responsible for retention.
    return asyncio.create_task(_loop(), name=task_name)
