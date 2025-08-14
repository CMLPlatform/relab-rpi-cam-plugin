"""Simple task repetition utilities."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def repeat_task(
    task_func: Callable[[], None] | Callable[[], Awaitable[None]],
    seconds: float,
    task_name: str,
) -> None:
    """Repeat a task every x seconds."""
    while True:
        try:
            if asyncio.iscoroutinefunction(task_func):
                await task_func()
            else:
                task_func()
            logger.info("Task '%s' executed successfully", task_name)
        except Exception:
            logger.exception("Exception in task '%s'", task_name)

        await asyncio.sleep(seconds)
