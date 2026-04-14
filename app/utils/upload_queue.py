"""Persistent upload queue for captures pending retry against the backend.

When a synchronous push from ``capture_jpeg`` fails (network blip, backend
briefly offline, JWT clock drift), the bytes are not lost: the capture is
enqueued under ``data/queue/`` with its metadata, and a background worker
drains the queue with exponential backoff. Entries that exhaust all retries
move to ``data/queue/dead/`` for manual recovery.

The queue format is intentionally simple so a human can inspect it:
  data/queue/{image_id}.jpg               — the captured bytes
  data/queue/{image_id}.json              — {capture_metadata, upload_metadata,
                                             filename, attempts, next_attempt_at}
  data/queue/dead/{image_id}.{jpg,json}   — dead-lettered entries
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from app.utils.backend_client import BackendUploadError, UploadedImageInfo, upload_image

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# Exponential backoff schedule. After the 5th failed attempt the entry is
# dead-lettered and no longer retried. Numbers are seconds.
_BACKOFF_SCHEDULE: tuple[int, ...] = (5, 30, 5 * 60, 30 * 60, 2 * 60 * 60)
_MAX_ATTEMPTS = len(_BACKOFF_SCHEDULE)
_WORKER_POLL_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class QueuedCapture:
    """An entry staged for backend retry."""

    image_id: str
    image_path: Path
    metadata_path: Path
    filename: str
    capture_metadata: Mapping[str, object]
    upload_metadata: Mapping[str, object]
    attempts: int
    next_attempt_at: datetime


class UploadQueue:
    """File-backed queue for pending backend uploads."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._dead_root = root / "dead"
        self._root.mkdir(parents=True, exist_ok=True)
        self._dead_root.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────

    async def enqueue(
        self,
        *,
        image_id: str,
        image_path: Path,
        filename: str,
        capture_metadata: Mapping[str, object],
        upload_metadata: Mapping[str, object],
    ) -> QueuedCapture:
        """Move a captured file into the queue for retry."""
        target_image = self._root / f"{image_id}.jpg"
        target_metadata = self._root / f"{image_id}.json"

        await asyncio.to_thread(shutil.move, str(image_path), str(target_image))

        now = datetime.now(UTC)
        entry_data = {
            "image_id": image_id,
            "filename": filename,
            "capture_metadata": dict(capture_metadata),
            "upload_metadata": dict(upload_metadata),
            "attempts": 0,
            "next_attempt_at": now.isoformat(),
        }
        await asyncio.to_thread(target_metadata.write_text, json.dumps(entry_data, indent=2))

        logger.info("Enqueued capture %s for backend retry", image_id)
        return QueuedCapture(
            image_id=image_id,
            image_path=target_image,
            metadata_path=target_metadata,
            filename=filename,
            capture_metadata=capture_metadata,
            upload_metadata=upload_metadata,
            attempts=0,
            next_attempt_at=now,
        )

    def iter_pending(self) -> list[QueuedCapture]:
        """Return all pending entries, cheapest first (soonest next_attempt_at)."""
        entries: list[QueuedCapture] = []
        for meta_path in sorted(self._root.glob("*.json")):
            entry = self._load_entry(meta_path)
            if entry is not None:
                entries.append(entry)
        entries.sort(key=lambda e: e.next_attempt_at)
        return entries

    def is_due(self, entry: QueuedCapture, *, now: datetime | None = None) -> bool:
        """Whether the entry's next_attempt_at has arrived."""
        reference = now or datetime.now(UTC)
        return entry.next_attempt_at <= reference

    async def mark_attempt_failed(self, entry: QueuedCapture) -> bool:
        """Record a failed attempt. Returns True if the entry was dead-lettered."""
        attempts = entry.attempts + 1
        if attempts >= _MAX_ATTEMPTS:
            await self._dead_letter(entry)
            logger.warning("Capture %s dead-lettered after %d failed attempts", entry.image_id, attempts)
            return True

        backoff = _BACKOFF_SCHEDULE[attempts]
        next_attempt = datetime.now(UTC) + timedelta(seconds=backoff)
        await self._persist_metadata(
            entry.metadata_path,
            image_id=entry.image_id,
            filename=entry.filename,
            capture_metadata=entry.capture_metadata,
            upload_metadata=entry.upload_metadata,
            attempts=attempts,
            next_attempt_at=next_attempt,
        )
        logger.info(
            "Capture %s upload attempt %d failed; retrying in %ds",
            entry.image_id,
            attempts,
            backoff,
        )
        return False

    async def mark_attempt_succeeded(self, entry: QueuedCapture) -> None:
        """Delete a successfully-uploaded entry from disk."""
        await asyncio.to_thread(_unlink_quiet, entry.image_path)
        await asyncio.to_thread(_unlink_quiet, entry.metadata_path)
        logger.info("Capture %s drained from queue", entry.image_id)

    async def drain_once(self) -> int:
        """Attempt every due entry exactly once. Returns the number of successes."""
        successes = 0
        for entry in self.iter_pending():
            if not self.is_due(entry):
                continue
            try:
                image_bytes = await asyncio.to_thread(entry.image_path.read_bytes)
                result: UploadedImageInfo = await upload_image(
                    image_bytes=image_bytes,
                    filename=entry.filename,
                    capture_metadata=entry.capture_metadata,
                    upload_metadata=entry.upload_metadata,
                )
            except BackendUploadError as exc:
                logger.debug("Queue drain: %s still failing: %s", entry.image_id, exc)
                await self.mark_attempt_failed(entry)
                continue
            except OSError as exc:
                logger.exception("Queue drain: %s file unreadable: %s", entry.image_id, exc)
                await self.mark_attempt_failed(entry)
                continue

            await self.mark_attempt_succeeded(entry)
            logger.info("Queue drain: %s uploaded as backend id %s", entry.image_id, result.image_id)
            successes += 1
        return successes

    # ── Internal ──────────────────────────────────────────────────────────

    def _load_entry(self, metadata_path: Path) -> QueuedCapture | None:
        try:
            payload = json.loads(metadata_path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("Queue: skipping unreadable metadata %s", metadata_path)
            return None
        image_id = payload.get("image_id") or metadata_path.stem
        image_path = self._root / f"{image_id}.jpg"
        if not image_path.exists():
            logger.warning("Queue: metadata %s has no matching jpg; cleaning up", metadata_path)
            _unlink_quiet(metadata_path)
            return None
        try:
            next_attempt_at = datetime.fromisoformat(payload.get("next_attempt_at", ""))
        except ValueError:
            next_attempt_at = datetime.now(UTC)
        return QueuedCapture(
            image_id=image_id,
            image_path=image_path,
            metadata_path=metadata_path,
            filename=payload.get("filename", f"{image_id}.jpg"),
            capture_metadata=payload.get("capture_metadata", {}),
            upload_metadata=payload.get("upload_metadata", {}),
            attempts=int(payload.get("attempts", 0)),
            next_attempt_at=next_attempt_at,
        )

    async def _persist_metadata(
        self,
        metadata_path: Path,
        *,
        image_id: str,
        filename: str,
        capture_metadata: Mapping[str, object],
        upload_metadata: Mapping[str, object],
        attempts: int,
        next_attempt_at: datetime,
    ) -> None:
        payload = {
            "image_id": image_id,
            "filename": filename,
            "capture_metadata": dict(capture_metadata),
            "upload_metadata": dict(upload_metadata),
            "attempts": attempts,
            "next_attempt_at": next_attempt_at.isoformat(),
        }
        await asyncio.to_thread(metadata_path.write_text, json.dumps(payload, indent=2))

    async def _dead_letter(self, entry: QueuedCapture) -> None:
        dead_image = self._dead_root / entry.image_path.name
        dead_metadata = self._dead_root / entry.metadata_path.name
        await asyncio.to_thread(shutil.move, str(entry.image_path), str(dead_image))
        await asyncio.to_thread(shutil.move, str(entry.metadata_path), str(dead_metadata))


def _unlink_quiet(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


class UploadQueueWorker:
    """Background task that drains an UploadQueue on a loop."""

    def __init__(self, queue: UploadQueue, *, poll_interval_s: float = _WORKER_POLL_INTERVAL_SECONDS) -> None:
        self._queue = queue
        self._poll_interval_s = poll_interval_s
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="upload-queue-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._queue.drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Upload queue drain failed; continuing")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_s)
            except TimeoutError:
                continue
