"""Temporary file utilities."""

import asyncio
import contextlib
import json
import os
import tempfile
import time
from pathlib import Path

from app.core.settings import settings


def is_running_in_container() -> bool:
    """Detect if we're running in a Docker container."""
    return Path("/.dockerenv").exists()


async def setup_directory(path: Path) -> Path:
    """Ensure a directory exists with 0o775 permissions."""
    await asyncio.to_thread(path.mkdir, parents=True, mode=0o775, exist_ok=True)
    return path


async def clear_directory(path: Path, *, time_to_live_s: int | None = None) -> None:
    """Delete all files in a directory, optionally only those older than a TTL."""

    def _clear() -> None:
        if not path.exists():
            return
        now = time.time()
        for file in path.glob("*"):
            if not file.is_file():
                continue
            if time_to_live_s and (now - file.stat().st_mtime) < time_to_live_s:
                continue
            file.unlink()

    await asyncio.to_thread(_clear)


def write_json_atomic(path: Path, data: dict[str, object]) -> None:
    """Write data as JSON to path atomically with 0o600 permissions.

    Creates parent directories as needed. Uses a temp file + rename so the
    target path is never briefly world-readable and a crash mid-write leaves
    no truncated file at the destination.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(json.dumps(data, indent=2))
            tmp.flush()
            os.fchmod(tmp.fileno(), 0o600)
            os.fsync(tmp.fileno())
        tmp_path.replace(path)
    except OSError:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


async def cleanup_images() -> None:
    """Delete old images and thumbnails from the image path."""
    await clear_directory(settings.image_path, time_to_live_s=settings.image_ttl_s)
    await clear_directory(settings.image_path / "preview-thumbnail", time_to_live_s=settings.image_ttl_s)
