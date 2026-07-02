"""Persistent upload queue for captures pending retry against the backend.

When a synchronous push from ``capture_jpeg`` fails (network blip, backend
briefly offline, JWT clock drift), the bytes are not lost: the capture is
enqueued under ``data/queue/`` with its metadata, and a background worker
drains the queue with exponential backoff. Entries that exhaust all retries
move to ``data/queue/dead/`` for manual recovery.

The queue format is intentionally simple so a human can inspect it:
  data/queue/{image_id}.jpg               — the captured bytes
  data/queue/{image_id}.json              — {capture_metadata, upload_metadata,
                                             attempts, next_attempt_at}
  data/queue/dead/{image_id}.{jpg,json}   — dead-lettered entries
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.core.settings import settings
from app.delivery.base import ImageSink, ImageSinkError
from app.observability.logging import build_log_extra
from app.utils.file_policy import JPEG_CAPTURE_POLICY, CaptureFileValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

logger = logging.getLogger(__name__)

# Exponential backoff schedule. After the 5th failed attempt the entry is
# dead-lettered and no longer retried. Numbers are seconds.
_BACKOFF_SCHEDULE: tuple[int, ...] = (5, 30, 5 * 60, 30 * 60, 2 * 60 * 60)
_MAX_ATTEMPTS = len(_BACKOFF_SCHEDULE)
_WORKER_POLL_INTERVAL_SECONDS = 5.0
_IMAGE_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


class UploadQueueFullError(RuntimeError):
    """Raised when the persistent retry queue cannot accept another capture."""


@dataclass(frozen=True)
class QueuedCapture:
    """An entry staged for backend retry."""

    image_id: str
    image_path: Path
    metadata_path: Path
    capture_metadata: Mapping[str, object]
    upload_metadata: Mapping[str, object]
    attempts: int
    next_attempt_at: datetime


class UploadQueue:
    """File-backed queue for pending image sink uploads.

    The queue is sink-agnostic: on drain it calls ``sink.put(...)`` for each
    pending entry, so swapping the sink (backend ↔ S3) doesn't require any
    changes here. The same retry / dead-letter machinery covers both paths.
    """

    def __init__(
        self,
        root: Path,
        sink: ImageSink,
        *,
        max_pending_entries: int | None = None,
        max_pending_bytes: int | None = None,
        dead_max_entries: int | None = None,
    ) -> None:
        self._root = root
        self._sink = sink
        self._dead_root = root / "dead"
        self._max_pending_entries = max_pending_entries
        self._max_pending_bytes = max_pending_bytes
        self._dead_max_entries = dead_max_entries
        self._root.mkdir(parents=True, exist_ok=True)
        self._dead_root.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────

    async def enqueue(
        self,
        *,
        image_id: str,
        image_path: Path,
        capture_metadata: Mapping[str, object],
        upload_metadata: Mapping[str, object],
    ) -> QueuedCapture:
        """Move a captured file into the queue for retry."""
        _validate_image_id(image_id)
        filename = JPEG_CAPTURE_POLICY.filename_for(image_id)
        target_image = self._root / filename
        target_metadata = self._root / f"{image_id}.json"
        now = datetime.now(UTC)
        metadata_payload = _metadata_payload(
            image_id=image_id,
            capture_metadata=capture_metadata,
            upload_metadata=upload_metadata,
            attempts=0,
            next_attempt_at=now,
        )
        self._ensure_capacity_for(image_path=image_path, metadata_payload=metadata_payload)

        await asyncio.to_thread(shutil.move, str(image_path), str(target_image))

        await asyncio.to_thread(target_metadata.write_text, json.dumps(metadata_payload, indent=2))

        logger.info("Enqueued capture %s for backend retry", image_id, extra=build_log_extra())
        return QueuedCapture(
            image_id=image_id,
            image_path=target_image,
            metadata_path=target_metadata,
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

    async def prune_dead_letters(self) -> None:
        """Apply deterministic dead-letter retention by oldest-over-count."""
        await asyncio.to_thread(self._prune_dead_letters_sync)

    def is_due(self, entry: QueuedCapture, *, now: datetime | None = None) -> bool:
        """Whether the entry's next_attempt_at has arrived."""
        reference = now or datetime.now(UTC)
        return entry.next_attempt_at <= reference

    async def mark_attempt_failed(self, entry: QueuedCapture, reason: str | None = None) -> bool:
        """Record a failed attempt. Returns True if the entry was dead-lettered."""
        attempts = entry.attempts + 1
        if attempts >= _MAX_ATTEMPTS:
            await self._dead_letter(entry)
            logger.warning(
                "Capture %s dead-lettered after %d failed attempts",
                entry.image_id,
                attempts,
                extra=build_log_extra(),
            )
            return True

        backoff = _BACKOFF_SCHEDULE[attempts - 1]
        next_attempt = datetime.now(UTC) + timedelta(seconds=backoff)
        payload = _metadata_payload(
            image_id=entry.image_id,
            capture_metadata=entry.capture_metadata,
            upload_metadata=entry.upload_metadata,
            attempts=attempts,
            next_attempt_at=next_attempt,
        )
        await asyncio.to_thread(entry.metadata_path.write_text, json.dumps(payload, indent=2))
        logger.info(
            "Capture %s upload attempt %d failed (%s); retrying in %ds",
            entry.image_id,
            attempts,
            reason or "reason unavailable",
            backoff,
            extra=build_log_extra(),
        )
        return False

    async def mark_attempt_succeeded(self, entry: QueuedCapture) -> None:
        """Delete a successfully-uploaded entry from disk."""
        await asyncio.to_thread(entry.image_path.unlink, missing_ok=True)
        await asyncio.to_thread(entry.metadata_path.unlink, missing_ok=True)
        logger.info("Capture %s drained from queue", entry.image_id, extra=build_log_extra())

    async def drain_once(self) -> int:
        """Attempt every due entry exactly once. Returns the number of successes.

        Each entry is processed in its own try/except so a single poisonous entry
        cannot stop the rest of the queue from draining. ``asyncio.CancelledError``
        propagates (task shutdown) but every other exception — including
        ``TimeoutError`` on the sink upload — is treated as a failed attempt and
        eventually dead-letters via the normal retry schedule.
        """
        successes = 0
        for entry in self.iter_pending():
            if not self.is_due(entry):
                continue
            try:
                image_bytes = await asyncio.to_thread(entry.image_path.read_bytes)
                JPEG_CAPTURE_POLICY.validate_persisted_bytes(
                    image_bytes,
                    max_bytes=settings.max_capture_file_bytes,
                    max_pixels=settings.max_capture_pixels,
                )
                stored = await self._sink.put(
                    image_id=entry.image_id,
                    image_bytes=image_bytes,
                    filename=JPEG_CAPTURE_POLICY.filename_for(entry.image_id),
                    capture_metadata=entry.capture_metadata,
                    upload_metadata=entry.upload_metadata,
                )
            except asyncio.CancelledError:
                raise
            except ImageSinkError as exc:
                await self.mark_attempt_failed(entry, reason=f"sink error: {exc}")
                continue
            except TimeoutError as exc:
                await self.mark_attempt_failed(entry, reason=f"timeout: {exc}" if str(exc) else "timeout")
                continue
            except OSError as exc:
                logger.exception("Queue drain: %s file unreadable", entry.image_id, extra=build_log_extra())
                await self.mark_attempt_failed(entry, reason=f"file unreadable: {exc}")
                continue
            except CaptureFileValidationError as exc:
                logger.warning("Queue drain: %s failed media validation", entry.image_id, extra=build_log_extra())
                await self.mark_attempt_failed(entry, reason=f"media validation: {exc}")
                continue
            except Exception as exc:
                # Unexpected exception: don't let one poisoned entry kill the drain
                # pass. Log with stacktrace, mark as failed, and keep going.
                logger.exception("Queue drain: %s hit unexpected error", entry.image_id, extra=build_log_extra())
                with contextlib.suppress(Exception):
                    await self.mark_attempt_failed(entry, reason=f"{type(exc).__name__}: {exc}")
                continue

            await self.mark_attempt_succeeded(entry)
            logger.info(
                "Queue drain: %s uploaded as stored id %s",
                entry.image_id,
                stored.image_id,
                extra=build_log_extra(),
            )
            successes += 1
        return successes

    # ── Internal ──────────────────────────────────────────────────────────

    def _load_entry(self, metadata_path: Path) -> QueuedCapture | None:
        """Load an entry from a metadata path, validating the presence of the image and parsing the fields."""
        try:
            payload = json.loads(metadata_path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("Queue: skipping unreadable metadata %s", metadata_path, extra=build_log_extra())
            return None
        image_id = metadata_path.stem
        if not _is_valid_image_id(image_id):
            logger.warning(
                "Queue: metadata %s has invalid image_id; cleaning up",
                metadata_path,
                extra=build_log_extra(),
            )
            _unlink_queue_pair(metadata_path)
            return None
        filename = JPEG_CAPTURE_POLICY.filename_for(image_id)
        image_path = self._root / filename
        if not image_path.exists():
            logger.warning(
                "Queue: metadata %s has no matching jpg; cleaning up",
                metadata_path,
                extra=build_log_extra(),
            )
            metadata_path.unlink(missing_ok=True)
            return None
        try:
            next_attempt_at = datetime.fromisoformat(payload.get("next_attempt_at", ""))
        except ValueError:
            next_attempt_at = datetime.now(UTC)
        return QueuedCapture(
            image_id=image_id,
            image_path=image_path,
            metadata_path=metadata_path,
            capture_metadata=payload.get("capture_metadata", {}),
            upload_metadata=payload.get("upload_metadata", {}),
            attempts=int(payload.get("attempts", 0)),
            next_attempt_at=next_attempt_at,
        )

    async def _dead_letter(self, entry: QueuedCapture) -> None:
        """Move an entry to the dead-letter area, removing it from the active queue."""
        dead_image = self._dead_root / entry.image_path.name
        dead_metadata = self._dead_root / entry.metadata_path.name
        await asyncio.to_thread(shutil.move, str(entry.image_path), str(dead_image))
        await asyncio.to_thread(shutil.move, str(entry.metadata_path), str(dead_metadata))
        await self.prune_dead_letters()

    def _ensure_capacity_for(self, *, image_path: Path, metadata_payload: dict[str, object]) -> None:
        pending_entries = self.iter_pending()
        if self._max_pending_entries is not None and len(pending_entries) >= self._max_pending_entries:
            msg = f"Upload queue is full: {len(pending_entries)} pending entries"
            raise UploadQueueFullError(msg)
        if self._max_pending_bytes is None:
            return
        pending_bytes = _directory_bytes(self._root)
        incoming_bytes = _path_size(image_path) + len(json.dumps(metadata_payload, indent=2).encode())
        if pending_bytes + incoming_bytes > self._max_pending_bytes:
            msg = f"Upload queue is full: {pending_bytes + incoming_bytes} bytes would exceed limit"
            raise UploadQueueFullError(msg)

    def _prune_dead_letters_sync(self) -> None:
        entries = _dead_letter_entries(self._dead_root)
        if self._dead_max_entries is None or len(entries) <= self._dead_max_entries:
            return
        for image_path, metadata_path, _mtime in entries[: len(entries) - self._dead_max_entries]:
            image_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)


def _is_valid_image_id(image_id: str) -> bool:
    return bool(_IMAGE_ID_PATTERN.fullmatch(image_id))


def _validate_image_id(image_id: str) -> None:
    if not _is_valid_image_id(image_id):
        msg = "image_id must be a lowercase 32-character hexadecimal string"
        raise ValueError(msg)


def _unlink_queue_pair(metadata_path: Path) -> None:
    metadata_path.unlink(missing_ok=True)
    metadata_path.with_suffix(".jpg").unlink(missing_ok=True)


def _path_size(path: Path) -> int:
    with contextlib.suppress(OSError):
        return path.stat().st_size
    return 0


def _directory_bytes(root: Path) -> int:
    return sum(_path_size(p) for p in (*root.glob("*.jpg"), *root.glob("*.json")))


def _dead_letter_entries(dead_root: Path) -> list[tuple[Path, Path, float]]:
    entries: list[tuple[Path, Path, float]] = []
    for metadata_path in dead_root.glob("*.json"):
        if not _is_valid_image_id(metadata_path.stem):
            _unlink_queue_pair(metadata_path)
            continue
        image_path = dead_root / f"{metadata_path.stem}.jpg"
        if not image_path.exists():
            metadata_path.unlink(missing_ok=True)
            continue
        mtime = min(_path_mtime(image_path), _path_mtime(metadata_path))
        entries.append((image_path, metadata_path, mtime))
    entries.sort(key=lambda entry: entry[2])
    return entries


def _path_mtime(path: Path) -> float:
    with contextlib.suppress(OSError):
        return path.stat().st_mtime
    return 0.0


def _metadata_payload(
    *,
    image_id: str,
    capture_metadata: Mapping[str, object],
    upload_metadata: Mapping[str, object],
    attempts: int,
    next_attempt_at: datetime,
) -> dict[str, object]:
    return {
        "image_id": image_id,
        "capture_metadata": dict(capture_metadata),
        "upload_metadata": dict(upload_metadata),
        "attempts": attempts,
        "next_attempt_at": next_attempt_at.isoformat(),
    }


async def run_upload_queue_worker(
    queue: UploadQueue,
    *,
    poll_interval_s: float = _WORKER_POLL_INTERVAL_SECONDS,
) -> None:
    """Drain the queue on a loop until the owning runtime cancels us."""
    while True:
        try:
            await queue.drain_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Upload queue drain failed; continuing", extra=build_log_extra())
        await asyncio.sleep(poll_interval_s)
