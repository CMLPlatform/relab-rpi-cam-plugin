"""Tests for immutable API-key snapshots used by auth checks."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.auth import dependencies as auth_mod
from app.core.runtime_state import RuntimeState

SNAPSHOT_KEY_1 = "snapshot-key-1"
SNAPSHOT_KEY_2 = "snapshot-key-2"


class _FixedClock:
    """Patchable replacement for the datetime class in auth.dependencies."""

    def __init__(self, fixed: datetime) -> None:
        self.fixed = fixed

    def now(self, tz: Any = None) -> datetime:  # noqa: ARG002
        return self.fixed


class TestAuthorizedSnapshot:
    """Tests for immutable request-scoped auth snapshots."""

    def test_reload_authorized_hashes_returns_immutable_snapshot(self) -> None:
        """Reload should return a stable snapshot that only changes when reloaded."""
        runtime_state = RuntimeState(authorized_api_keys=frozenset({SNAPSHOT_KEY_1}))

        snapshot = auth_mod.reload_authorized_hashes(runtime_state)
        assert auth_mod._is_authorized(SNAPSHOT_KEY_1, snapshot) is True

        runtime_state.add_authorized_api_key(SNAPSHOT_KEY_2)
        assert auth_mod._is_authorized(SNAPSHOT_KEY_2, snapshot) is False

        refreshed_snapshot = auth_mod.reload_authorized_hashes(runtime_state)
        assert auth_mod._is_authorized(SNAPSHOT_KEY_2, refreshed_snapshot) is True

    def test_authorization_compares_every_candidate_before_returning(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API-key checks should not leak the matching candidate's position."""
        candidates = frozenset({"first-match", "second-key", "third-key"})
        compared: list[str] = []

        def _compare_digest(left: str, right: str) -> bool:
            del left
            compared.append(right)
            return True

        monkeypatch.setattr(auth_mod.hmac, "compare_digest", _compare_digest)

        assert auth_mod._is_authorized("first-match", candidates) is True
        assert set(compared) == candidates
        assert len(compared) == len(candidates)


class TestBrowserSessions:
    """Tests for server-side browser session lifetime policy."""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self) -> None:
        auth_mod._active_sessions.clear()

    def test_valid_session_activity_refreshes_inactivity_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A valid session should stay usable when activity occurs inside the inactivity window."""
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
        clock = _FixedClock(created_at)
        monkeypatch.setattr(auth_mod, "datetime", clock)
        token = auth_mod.create_session()

        clock.fixed = created_at + timedelta(minutes=29)
        assert auth_mod.has_valid_session(token) is True

        clock.fixed = created_at + timedelta(minutes=58)
        assert auth_mod.has_valid_session(token) is True

    def test_session_expires_after_inactivity_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Idle sessions should be invalidated before the absolute lifetime."""
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
        expired_at = created_at + auth_mod.SESSION_INACTIVITY_TIMEOUT + timedelta(seconds=1)
        clock = _FixedClock(created_at)
        monkeypatch.setattr(auth_mod, "datetime", clock)
        token = auth_mod.create_session()

        clock.fixed = expired_at
        assert auth_mod.has_valid_session(token) is False
        assert token not in auth_mod._active_sessions

    def test_activity_does_not_extend_absolute_lifetime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recent activity should not keep a session valid past its absolute lifetime."""
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
        clock = _FixedClock(created_at)
        monkeypatch.setattr(auth_mod, "datetime", clock)
        token = auth_mod.create_session()

        for active_at in (created_at + timedelta(minutes=29 * step) for step in range(1, 25)):
            clock.fixed = active_at
            assert auth_mod.has_valid_session(token) is True

        clock.fixed = created_at + auth_mod.SESSION_ABSOLUTE_LIFETIME + timedelta(seconds=1)
        assert auth_mod.has_valid_session(token) is False
        assert token not in auth_mod._active_sessions
