"""Shared fixtures and collection rules for the test suite."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import reload_authorized_hashes, verify_request
from app.api.services.camera_manager import CameraManager
from app.core.runtime import AppRuntime, set_active_runtime
from app.main import app
from tests.support.fakes import build_test_runtime, make_camera_manager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from _pytest.nodes import Item
    from fastapi import FastAPI

TEST_API_KEY = "test-api-key-12345"
_SLOW_TEST_FRAGMENTS = (
    "tests/unit/test_upload_queue.py::TestUploadQueueWorker",
    "tests/unit/test_thermal_governor.py::TestLifecycle",
    "tests/integration/test_main_lifespan.py",
)


def _ensure_test_api_key() -> None:
    """Make the standard test API key available to auth-protected routes."""
    return


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers used by this repo's suite."""
    config.addinivalue_line("markers", "unit: pure function/service tests with no app boot unless unavoidable")
    config.addinivalue_line("markers", "integration: ASGI app, route, or lifespan behavior tests")
    config.addinivalue_line("markers", "slow: intentionally longer lifecycle/worker tests")


def pytest_collection_modifyitems(items: list[Item]) -> None:
    """Auto-mark tests based on their file path and execution shape."""
    for item in items:
        path = str(item.fspath)
        if f"{Path('tests') / 'unit'}" in path:
            item.add_marker(pytest.mark.unit)
        if f"{Path('tests') / 'integration'}" in path:
            item.add_marker(pytest.mark.integration)
        if any(fragment in item.nodeid for fragment in _SLOW_TEST_FRAGMENTS):
            item.add_marker(pytest.mark.slow)


@pytest.fixture(scope="session", autouse=True)
def _authorized_test_api_key() -> None:
    """Seed the test auth key once for the whole suite."""
    _ensure_test_api_key()


@pytest.fixture
def camera_manager() -> CameraManager:
    """Return a camera manager with a typed fake provider-neutral backend."""
    return make_camera_manager()


@pytest.fixture
def app_runtime(camera_manager: CameraManager) -> AppRuntime:
    """Return a runtime wired to the test camera manager."""
    runtime = build_test_runtime(camera_manager=camera_manager)
    runtime.runtime_state.add_authorized_api_key(TEST_API_KEY)
    reload_authorized_hashes(runtime.runtime_state)
    return runtime


@pytest.fixture
def test_app(app_runtime: AppRuntime) -> Iterator[FastAPI]:
    """Attach a fresh runtime to the global FastAPI app for one test."""
    original_runtime = getattr(app.state, "runtime", None)
    original_overrides = dict(app.dependency_overrides)
    app.state.runtime = app_runtime
    set_active_runtime(app_runtime)
    app.dependency_overrides.clear()
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        if original_runtime is None:
            if hasattr(app.state, "runtime"):
                delattr(app.state, "runtime")
        else:
            app.state.runtime = original_runtime
            set_active_runtime(original_runtime)
        if original_runtime is None:
            set_active_runtime(None)


@pytest.fixture
async def client(test_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Async test client with auth override and runtime-backed dependencies."""

    async def _override_auth() -> str:
        return TEST_API_KEY

    test_app.dependency_overrides[verify_request] = _override_auth
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def unauthed_client(test_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Async test client without auth override."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
