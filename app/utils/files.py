"""Temporary file utilities."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings


def _glob_files(path: Path) -> list[Path]:
    """List files matching glob pattern (non-async helper for use with asyncio.to_thread)."""
    return list(path.glob("*"))


async def setup_directory(path: Path) -> Path:
    """Set up directory."""
    try:
        await asyncio.to_thread(path.mkdir, parents=True, mode=0o775, exist_ok=True)
    except OSError as e:
        err_msg = f"Failed to create directory: {e}"
        raise RuntimeError(err_msg) from e
    return path


async def clear_directory(path: Path, *, time_to_live_s: int | None = None) -> None:
    """Clear expired files in directory."""
    if not await asyncio.to_thread(path.exists):
        return

    now = datetime.now(UTC).timestamp()
    files = await asyncio.to_thread(_glob_files, path)
    for file in files:
        if not await asyncio.to_thread(file.is_file):
            continue

        stat = await asyncio.to_thread(file.stat)
        if time_to_live_s and (now - stat.st_mtime) < time_to_live_s:
            continue

        await asyncio.to_thread(file.unlink)


async def cleanup_images() -> None:
    """Clean up expired images."""
    await clear_directory(settings.image_path, time_to_live_s=settings.image_ttl_s)
