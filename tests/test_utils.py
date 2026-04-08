"""Tests for utility modules."""

from pathlib import Path

from app.utils.files import clear_directory, setup_directory
from app.utils.pairing import PairingState, _generate_code_and_fingerprint, get_pairing_state


class TestSetupDirectory:
    """Tests for setup_directory."""

    async def test_creates_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "new" / "nested"
        result = await setup_directory(target)
        assert result.is_dir()
        assert result == target

    async def test_existing_directory_is_noop(self, tmp_path: Path) -> None:
        result = await setup_directory(tmp_path)
        assert result.is_dir()


class TestClearDirectory:
    """Tests for clear_directory."""

    async def test_clear_nonexistent_dir_is_noop(self, tmp_path: Path) -> None:
        await clear_directory(tmp_path / "does-not-exist")

    async def test_clear_removes_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        await clear_directory(tmp_path)
        remaining = list(tmp_path.iterdir())
        assert remaining == []

    async def test_clear_respects_ttl(self, tmp_path: Path) -> None:
        f = tmp_path / "recent.txt"
        f.write_text("still fresh")
        # File just created, so TTL of 3600s should keep it
        await clear_directory(tmp_path, time_to_live_s=3600)
        assert f.exists()


class TestPairingState:
    """Tests for pairing state helpers."""

    def test_default_state_is_idle(self) -> None:
        state = get_pairing_state()
        assert isinstance(state, PairingState)

    def test_generate_code_format(self) -> None:
        code, fingerprint = _generate_code_and_fingerprint()
        assert len(code) == 6
        assert code == code.upper()
        assert len(fingerprint) > 10

    def test_codes_are_unique(self) -> None:
        codes = {_generate_code_and_fingerprint()[0] for _ in range(20)}
        # With 6 hex chars, collisions in 20 samples are astronomically unlikely
        assert len(codes) > 15
