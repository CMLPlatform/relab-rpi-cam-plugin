"""Tests for utility modules."""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.utils.files import cleanup_images, clear_directory, setup_directory
from app.utils.pairing import PairingState, _generate_code_and_fingerprint, get_pairing_state


def _list_dir(path: Path) -> list[Path]:
    """List directory contents (non-async helper for use with asyncio.to_thread)."""
    return list(path.iterdir())


class TestSetupDirectory:
    """Tests for setup_directory."""

    async def test_creates_directory(self, tmp_path: Path) -> None:
        """Should create the target directory if it doesn't exist."""
        target = tmp_path / "new" / "nested"
        result = await setup_directory(target)
        assert result.is_dir()
        assert result == target

    async def test_existing_directory_is_noop(self, tmp_path: Path) -> None:
        """Should do nothing if the target directory already exists."""
        result = await setup_directory(tmp_path)
        assert result.is_dir()

    async def test_oserror_is_wrapped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should raise RuntimeError if os.makedirs fails."""
        monkeypatch.setattr("app.utils.files.asyncio.to_thread", AsyncMock(side_effect=OSError("nope")))
        with pytest.raises(RuntimeError, match="Failed to create directory"):
            await setup_directory(tmp_path)


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
        """cleanup_images should call clear_directory with the configured image_path."""
        monkeypatch.setattr(settings, "image_path", tmp_path)
        clear_mock = AsyncMock()
        monkeypatch.setattr("app.utils.files.clear_directory", clear_mock)
        await cleanup_images()
        clear_mock.assert_awaited_once_with(tmp_path, time_to_live_s=settings.image_ttl_s)


class TestPairingState:
    """Tests for pairing state helpers."""

    def test_default_state_is_idle(self) -> None:
        """Default pairing state should be IDLE."""
        state = get_pairing_state()
        assert isinstance(state, PairingState)

    def test_generate_code_format(self) -> None:
        """Generated code should be 6 uppercase hex characters and fingerprint should be a string."""
        code, fingerprint = _generate_code_and_fingerprint()
        assert len(code) == 6
        assert code == code.upper()
        assert len(fingerprint) > 10

    def test_codes_are_unique(self) -> None:
        """Multiple generated codes should be unique."""
        codes = {_generate_code_and_fingerprint()[0] for _ in range(20)}
        # With 6 hex chars, collisions in 20 samples are astronomically unlikely
        assert len(codes) > 15
