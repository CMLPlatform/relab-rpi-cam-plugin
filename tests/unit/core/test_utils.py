"""Tests for utility modules."""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.settings import settings
from app.pairing.services.service import _generate_code_and_fingerprint
from app.utils.files import cleanup_images, clear_directory
from relab_rpi_cam_models import PAIRING_CODE_ALPHABET, PAIRING_CODE_LENGTH


def _list_dir(path: Path) -> list[Path]:
    """List directory contents (non-async helper for use with asyncio.to_thread)."""
    return list(path.iterdir())


class TestClearDirectory:
    """Tests for clear_directory."""

    async def test_clear_nonexistent_dir_is_noop(self, tmp_path: Path) -> None:
        """Should do nothing if the target directory doesn't exist."""
        await clear_directory(tmp_path / "does-not-exist")

    async def test_clear_removes_files(self, tmp_path: Path) -> None:
        """Should remove files older than TTL and keep newer ones."""
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        await clear_directory(tmp_path)
        remaining = await asyncio.to_thread(_list_dir, tmp_path)
        assert remaining == []

    async def test_clear_respects_ttl(self, tmp_path: Path) -> None:
        """Should only remove files older than the specified TTL."""
        f = tmp_path / "recent.txt"
        f.write_text("still fresh")
        # File just created, so TTL of 3600s should keep it
        await clear_directory(tmp_path, time_to_live_s=3600)
        assert f.exists()

    async def test_clear_skips_directories(self, tmp_path: Path) -> None:
        """Should not delete subdirectories."""
        nested = tmp_path / "nested"
        nested.mkdir()
        old_file = tmp_path / "old.txt"
        old_file.write_text("delete me")
        old_mtime = datetime.now(UTC).timestamp() - 10_000
        os.utime(old_file, (old_mtime, old_mtime))
        await clear_directory(tmp_path, time_to_live_s=1)
        assert nested.exists()
        assert not old_file.exists()

    async def test_cleanup_images_uses_image_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cleanup_images should clean captured images and cached preview thumbnails."""
        monkeypatch.setattr(settings, "image_path", tmp_path)
        clear_mock = AsyncMock()
        monkeypatch.setattr("app.utils.files.clear_directory", clear_mock)
        await cleanup_images()
        assert clear_mock.await_args_list == [
            ((tmp_path,), {"time_to_live_s": settings.image_ttl_s}),
            ((tmp_path / "preview-thumbnail",), {"time_to_live_s": settings.image_ttl_s}),
        ]


class TestPairingState:
    """Tests for pairing state helpers."""

    def test_generate_code_format(self) -> None:
        """Generated code should be 6 unambiguous uppercase characters."""
        code, fingerprint = _generate_code_and_fingerprint()
        assert len(code) == PAIRING_CODE_LENGTH
        assert set(code) <= set(PAIRING_CODE_ALPHABET)
        assert len(fingerprint) > 10

    def test_codes_are_unique(self) -> None:
        """Multiple generated codes should be unique."""
        codes = {_generate_code_and_fingerprint()[0] for _ in range(20)}
        # With the unambiguous 32-character alphabet, collisions in 20 samples are very unlikely.
        assert len(codes) > 15
