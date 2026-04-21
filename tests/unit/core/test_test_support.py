"""Tests for shared test-support helpers."""

from app.core.runtime import AppRuntime
from tests.support.fakes import (
    FakeBackend,
    FakePairingService,
    FakeRelayService,
    build_test_runtime,
    make_camera_manager,
)


class TestBuildTestRuntime:
    """Shared runtime factory should produce current-architecture collaborators."""

    def test_builds_runtime_with_fake_services(self) -> None:
        """The shared runtime builder should wire runtime-owned fake services."""
        runtime = build_test_runtime()
        assert isinstance(runtime, AppRuntime)
        assert isinstance(runtime.relay_service, FakeRelayService)
        assert isinstance(runtime.pairing_service, FakePairingService)

    def test_uses_typed_fake_backend_for_camera_manager(self) -> None:
        """The default camera manager should use the shared fake backend."""
        manager = make_camera_manager()
        assert isinstance(manager.backend, FakeBackend)
