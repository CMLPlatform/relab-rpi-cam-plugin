"""Tests for immutable API-key snapshots used by auth checks."""

from app.api.dependencies import auth as auth_mod
from app.core.runtime_state import RuntimeState

SNAPSHOT_KEY_1 = "snapshot-key-1"
SNAPSHOT_KEY_2 = "snapshot-key-2"


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
