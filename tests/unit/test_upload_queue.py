"""Tests for the upload queue."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import AnyUrl

from app.api.services.image_sinks.base import ImageSink, ImageSinkError, StoredImage
from app.utils.upload_queue import UploadQueue, UploadQueueWorker

if TYPE_CHECKING:
    from pathlib import Path


class _FakeSink:
    """Minimal ``ImageSink`` stub — returns a fixed ``StoredImage`` on every ``put``."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.put = AsyncMock(side_effect=self._put)

    async def _put(self, **_kwargs: object) -> StoredImage:
        if self._fail:
            msg = "fake sink is failing"
            raise ImageSinkError(msg)
        return StoredImage(image_id="srv-1", image_url=AnyUrl("https://example.com/img.jpg"))


@pytest.fixture
def queue_root(tmp_path: Path) -> Path:
    """A clean upload-queue root directory."""
    return tmp_path / "queue"


@pytest.fixture
def sink() -> _FakeSink:
    """A happy-path sink that always succeeds."""
    return _FakeSink(fail=False)


@pytest.fixture
def failing_sink() -> _FakeSink:
    """A sink that always raises ``ImageSinkError``."""
    return _FakeSink(fail=True)


@pytest.fixture
def sample_image(tmp_path: Path) -> Path:
    """A small fake JPEG file outside the queue root."""
    path = tmp_path / "capture.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fake-jpg-body")
    return path


class TestEnqueue:
    """Tests for UploadQueue.enqueue."""

    async def test_moves_file_and_writes_metadata(
        self,
        queue_root: Path,
        sample_image: Path,
        sink: _FakeSink,
    ) -> None:
        """Enqueue should move the jpg into the queue root and write a metadata sidecar."""
        queue = UploadQueue(queue_root, sink=cast("ImageSink", sink))
        entry = await queue.enqueue(
            image_id="abc123",
            image_path=sample_image,
            filename="abc123.jpg",
            capture_metadata={"width": 1920},
            upload_metadata={"product_id": 42},
        )

        assert not await asyncio.to_thread(sample_image.exists)
        assert entry.image_path == queue_root / "abc123.jpg"
        assert entry.metadata_path == queue_root / "abc123.json"
        assert await asyncio.to_thread(entry.image_path.exists)
        assert await asyncio.to_thread(entry.metadata_path.exists)

    async def test_creates_queue_and_dead_directories(
        self,
        queue_root: Path,
        sink: _FakeSink,
    ) -> None:
        """Instantiating the queue should create both the root and the dead-letter subdir."""
        assert not await asyncio.to_thread(queue_root.exists)
        UploadQueue(queue_root, sink=cast("ImageSink", sink))
        assert await asyncio.to_thread(queue_root.is_dir)
        assert await asyncio.to_thread((queue_root / "dead").is_dir)


class TestIterPending:
    """Tests for iter_pending ordering + resilience."""

    async def test_returns_entries_sorted_by_next_attempt(
        self,
        queue_root: Path,
        sample_image: Path,
        sink: _FakeSink,
    ) -> None:
        """Entries with earlier next_attempt_at should come first."""
        queue = UploadQueue(queue_root, sink=cast("ImageSink", sink))

        # Enqueue one entry that's due now.
        await queue.enqueue(
            image_id="entry-a",
            image_path=sample_image,
            filename="entry-a.jpg",
            capture_metadata={},
            upload_metadata={},
        )

        # Manually add a second entry dated in the future.
        future = datetime.now(UTC) + timedelta(hours=1)
        image_b = queue_root / "entry-b.jpg"
        image_b.write_bytes(b"\xff\xd8bbb")
        (queue_root / "entry-b.json").write_text(
            f'{{"image_id": "entry-b", "filename": "entry-b.jpg", "capture_metadata": {{}}, '
            f'"upload_metadata": {{}}, "attempts": 1, "next_attempt_at": "{future.isoformat()}"}}'
        )

        entries = queue.iter_pending()
        assert [e.image_id for e in entries] == ["entry-a", "entry-b"]

    def test_skips_orphan_metadata(self, queue_root: Path, sink: _FakeSink) -> None:
        """A .json without a matching .jpg should be cleaned up, not yielded."""
        queue = UploadQueue(queue_root, sink=cast("ImageSink", sink))
        orphan = queue_root / "orphan.json"
        orphan.write_text('{"image_id": "orphan"}')
        assert queue.iter_pending() == []
        assert not orphan.exists()


class TestDrainOnce:
    """Tests for drain_once happy-path and failure-path."""

    async def test_successful_drain_removes_entry(
        self,
        queue_root: Path,
        sample_image: Path,
        sink: _FakeSink,
    ) -> None:
        """When the sink succeeds, the queue entry should be deleted."""
        queue = UploadQueue(queue_root, sink=cast("ImageSink", sink))
        entry = await queue.enqueue(
            image_id="happy",
            image_path=sample_image,
            filename="happy.jpg",
            capture_metadata={},
            upload_metadata={"product_id": 1},
        )

        successes = await queue.drain_once()

        assert successes == 1
        assert sink.put.await_count == 1
        assert not await asyncio.to_thread(entry.image_path.exists)
        assert not await asyncio.to_thread(entry.metadata_path.exists)

    async def test_failed_drain_increments_attempts(
        self,
        queue_root: Path,
        sample_image: Path,
        failing_sink: _FakeSink,
    ) -> None:
        """A failed sink put should bump attempts and schedule a later retry."""
        queue = UploadQueue(queue_root, sink=cast("ImageSink", failing_sink))
        entry = await queue.enqueue(
            image_id="sad",
            image_path=sample_image,
            filename="sad.jpg",
            capture_metadata={},
            upload_metadata={},
        )

        successes = await queue.drain_once()

        assert successes == 0
        assert failing_sink.put.await_count == 1
        assert await asyncio.to_thread(entry.image_path.exists)  # still present
        reloaded = queue.iter_pending()[0]
        assert reloaded.attempts == 1
        assert reloaded.next_attempt_at > datetime.now(UTC)

    async def test_skips_entries_not_yet_due(
        self,
        queue_root: Path,
        sink: _FakeSink,
    ) -> None:
        """Entries with next_attempt_at in the future must be ignored this pass."""
        queue = UploadQueue(queue_root, sink=cast("ImageSink", sink))
        future = datetime.now(UTC) + timedelta(hours=1)
        (queue_root / "waiting.jpg").write_bytes(b"\xff\xd8")
        (queue_root / "waiting.json").write_text(
            f'{{"image_id": "waiting", "filename": "waiting.jpg", "capture_metadata": {{}}, '
            f'"upload_metadata": {{}}, "attempts": 2, "next_attempt_at": "{future.isoformat()}"}}'
        )

        successes = await queue.drain_once()

        assert successes == 0
        assert sink.put.await_count == 0


class TestDeadLetter:
    """Tests for exhausting retries and dead-lettering."""

    async def test_dead_letters_after_max_attempts(
        self,
        queue_root: Path,
        sample_image: Path,
        sink: _FakeSink,
    ) -> None:
        """After _MAX_ATTEMPTS consecutive failures the entry should move under dead/."""
        from app.utils import upload_queue as upload_queue_mod  # noqa: PLC0415

        queue = UploadQueue(queue_root, sink=cast("ImageSink", sink))
        entry = await queue.enqueue(
            image_id="doomed",
            image_path=sample_image,
            filename="doomed.jpg",
            capture_metadata={},
            upload_metadata={},
        )

        max_attempts = upload_queue_mod._MAX_ATTEMPTS
        # Simulate attempts 1..(max_attempts - 1) — not yet dead.
        current = entry
        for attempt in range(1, max_attempts):
            dead = await queue.mark_attempt_failed(current)
            assert dead is False
            refreshed = queue.iter_pending()
            assert len(refreshed) == 1
            assert refreshed[0].attempts == attempt
            current = refreshed[0]

        # One more failure — dead-letter.
        dead = await queue.mark_attempt_failed(current)
        assert dead is True
        assert queue.iter_pending() == []
        assert await asyncio.to_thread((queue_root / "dead" / "doomed.jpg").exists)
        assert await asyncio.to_thread((queue_root / "dead" / "doomed.json").exists)


class TestUploadQueueWorker:
    """Tests for the background worker start/stop lifecycle."""

    async def test_start_then_stop_does_not_raise(self, queue_root: Path, sink: _FakeSink) -> None:
        """The worker should cleanly start and stop even with an empty queue."""
        queue = UploadQueue(queue_root, sink=cast("ImageSink", sink))
        worker = UploadQueueWorker(queue, poll_interval_s=0.01)
        worker.start()
        # Give the worker one tick to enter its loop.

        await asyncio.sleep(0.05)
        await worker.stop()
