"""Tests for the repeat_task utility."""

import asyncio

from app.utils.tasks import repeat_task


class TestRepeatTask:
    """Tests for the repeat_task helper."""

    async def test_executes_async_func(self) -> None:
        """Test that the task executes an async function repeatedly."""
        call_count = 0
        second_call = asyncio.Event()

        async def _increment() -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                second_call.set()

        task = repeat_task(_increment, seconds=0.01, task_name="test_async")
        await asyncio.wait_for(second_call.wait(), timeout=1)
        task.cancel()
        assert call_count >= 2

    async def test_executes_sync_func(self) -> None:
        """Test that the task executes a sync function repeatedly."""
        call_count = 0
        second_call = asyncio.Event()

        def _increment() -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                second_call.set()

        task = repeat_task(_increment, seconds=0.01, task_name="test_sync")
        await asyncio.wait_for(second_call.wait(), timeout=1)
        task.cancel()
        assert call_count >= 2

    async def test_exception_does_not_kill_loop(self) -> None:
        """Test that an exception in the task does not stop it from running again."""
        call_count = 0
        second_call = asyncio.Event()

        async def _flaky() -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                second_call.set()
            if call_count == 1:
                msg = "boom"
                raise RuntimeError(msg)

        task = repeat_task(_flaky, seconds=0.01, task_name="test_flaky")
        await asyncio.wait_for(second_call.wait(), timeout=1)
        task.cancel()
        # Should have continued past the first failure
        assert call_count >= 2
