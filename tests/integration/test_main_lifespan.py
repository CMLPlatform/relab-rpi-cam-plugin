"""Tests for application lifespan startup and shutdown."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

import app.main as main_mod
from app.core.config import settings


class DummyTask:
    """Small stand-in for asyncio.Task used in lifespan tests."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.cancelled = False

    def cancel(self) -> None:
        """Simulate cancelling the task."""
        self.cancelled = True

    def __hash__(self) -> int:
        return hash(self.name)


async def _run_lifespan_once(app: FastAPI) -> None:
    """Helper to run the lifespan of the app once."""
    async with main_mod.lifespan(app):
        await asyncio.sleep(0)


class TestLifespan:
    """Tests for the FastAPI lifespan hook."""

    async def test_relay_enabled_starts_relay_and_cleans_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that when relay credentials are set, the relay is started on startup and cleaned up on shutdown."""
        app = FastAPI()
        monkeypatch.setattr(settings, "relay_backend_url", "wss://example.com/ws")
        monkeypatch.setattr(settings, "relay_camera_id", "cam-1")
        monkeypatch.setattr(settings, "relay_api_key", "key-1")
        monkeypatch.setattr(settings, "pairing_backend_url", "")
        monkeypatch.setattr(main_mod, "apply_relay_credentials", lambda: None)
        setup_calls: list[object] = []

        async def _setup_directory(path: object) -> object:
            setup_calls.append(path)
            return path

        monkeypatch.setattr(main_mod, "setup_directory", _setup_directory)
        monkeypatch.setattr(main_mod, "repeat_task", lambda _task_func, _seconds, task_name: DummyTask(task_name))
        cleanup_mock = AsyncMock()
        monkeypatch.setattr(main_mod.camera_manager, "cleanup", cleanup_mock)
        monkeypatch.setattr(main_mod, "run_relay", lambda: asyncio.sleep(0))

        created: list[str | None] = []

        def _create_task(coro: object, name: str | None = None) -> asyncio.Task[object]:
            created.append(name)
            task_coro = cast("Any", coro)
            return asyncio.get_running_loop().create_task(task_coro, name=name)

        monkeypatch.setattr(asyncio, "create_task", _create_task)

        await _run_lifespan_once(app)

        assert setup_calls == [settings.image_path]
        assert created == ["ws_relay"]
        cleanup_mock.assert_awaited_once_with(force=True)

    async def test_pairing_mode_starts_relay_after_pairing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that when no relay credentials are set, the relay is started after pairing."""
        app = FastAPI()
        monkeypatch.setattr(settings, "relay_backend_url", "")
        monkeypatch.setattr(settings, "relay_camera_id", "")
        monkeypatch.setattr(settings, "relay_api_key", "")
        monkeypatch.setattr(settings, "pairing_backend_url", "https://example.com")
        monkeypatch.setattr(main_mod, "apply_relay_credentials", lambda: None)
        monkeypatch.setattr(main_mod, "setup_directory", AsyncMock())
        monkeypatch.setattr(main_mod, "repeat_task", lambda _task_func, _seconds, task_name: DummyTask(task_name))
        cleanup_mock = AsyncMock()
        monkeypatch.setattr(main_mod.camera_manager, "cleanup", cleanup_mock)
        monkeypatch.setattr(main_mod, "run_relay", lambda: asyncio.sleep(0))

        async def _run_pairing(callback: Callable[[], Awaitable[None]]) -> None:
            await callback()

        monkeypatch.setattr(main_mod, "run_pairing", _run_pairing)

        created: list[str | None] = []

        def _create_task(coro: object, name: str | None = None) -> asyncio.Task[object]:
            created.append(name)
            task_coro = cast("Any", coro)
            return asyncio.get_running_loop().create_task(task_coro, name=name)

        monkeypatch.setattr(asyncio, "create_task", _create_task)

        await _run_lifespan_once(app)

        assert created == ["pairing", "ws_relay"]
        cleanup_mock.assert_awaited_once_with(force=True)
