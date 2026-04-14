"""Tests for the lores preview pipeline manager."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.api.services import preview_pipeline as preview_pipeline_mod
from app.api.services.preview_pipeline import (
    PreviewPipelineManager,
    get_preview_pipeline_manager,
    reset_preview_pipeline_manager,
)


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    reset_preview_pipeline_manager()


@pytest.fixture
def stub_encoder_and_output(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace H264Encoder + FfmpegOutput with MagicMocks so start_encoder is safe."""
    encoder_cls = MagicMock(return_value=MagicMock(name="H264Encoder-instance"))
    output_factory = MagicMock(return_value=MagicMock(name="FfmpegOutput-instance"))
    monkeypatch.setattr(preview_pipeline_mod, "H264Encoder", encoder_cls)
    monkeypatch.setattr(preview_pipeline_mod, "_build_ffmpeg_output", output_factory)
    return encoder_cls


class TestSingleton:
    """get_preview_pipeline_manager should return a process-wide singleton."""

    def test_returns_same_instance(self) -> None:
        """Consecutive calls must yield the same manager."""
        a = get_preview_pipeline_manager()
        b = get_preview_pipeline_manager()
        assert a is b

    def test_reset_forces_new_instance(self) -> None:
        """reset_preview_pipeline_manager should clear the cached instance."""
        a = get_preview_pipeline_manager()
        reset_preview_pipeline_manager()
        b = get_preview_pipeline_manager()
        assert a is not b


class TestAcquireRelease:
    """Reference-counted start/stop."""

    async def test_first_acquire_starts_encoder(self, stub_encoder_and_output: MagicMock) -> None:
        """First subscriber triggers start_encoder on the lores stream."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.acquire(camera)

        assert manager.active_subscribers == 1
        assert manager.is_running
        camera.start_encoder.assert_called_once()
        assert camera.start_encoder.call_args.kwargs == {"name": "lores"}
        stub_encoder_and_output.assert_called_once()

    async def test_second_acquire_only_increments_refcount(self, stub_encoder_and_output: MagicMock) -> None:
        """Subsequent subscribers must not restart the encoder."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.acquire(camera)
        await manager.acquire(camera)

        assert manager.active_subscribers == 2
        camera.start_encoder.assert_called_once()

    async def test_release_below_zero_is_noop(self, stub_encoder_and_output: MagicMock) -> None:
        """Releasing with zero subscribers must not error or go negative."""
        manager = PreviewPipelineManager()
        camera = MagicMock()
        assert stub_encoder_and_output.called is False

        await manager.release(camera)

        assert manager.active_subscribers == 0
        camera.stop_encoder.assert_not_called()

    async def test_last_release_stops_encoder(self, stub_encoder_and_output: MagicMock) -> None:
        """Releasing the last subscriber detaches the encoder."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.acquire(camera)
        await manager.release(camera)

        assert manager.active_subscribers == 0
        assert not manager.is_running
        encoder = camera.start_encoder.call_args.args[0]
        camera.stop_encoder.assert_called_once_with(encoder)

    async def test_release_does_not_stop_while_other_subscribers_remain(
        self,
        stub_encoder_and_output: MagicMock,
    ) -> None:
        """Releasing one of many must keep the encoder running."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.acquire(camera)
        await manager.acquire(camera)
        await manager.release(camera)

        assert manager.active_subscribers == 1
        assert manager.is_running
        camera.stop_encoder.assert_not_called()


class TestForceStop:
    """force_stop shuts down regardless of the refcount."""

    async def test_force_stop_clears_refcount(self, stub_encoder_and_output: MagicMock) -> None:
        """force_stop should zero the refcount and tear down the encoder."""
        manager = PreviewPipelineManager()
        camera = MagicMock()
        await manager.acquire(camera)
        await manager.acquire(camera)

        await manager.force_stop(camera)

        assert manager.active_subscribers == 0
        assert not manager.is_running
        encoder = camera.start_encoder.call_args.args[0]
        camera.stop_encoder.assert_called_once_with(encoder)


class TestSetBitrate:
    """Thermal governor support: set_bitrate reconfigures the live encoder."""

    async def test_set_bitrate_while_idle_is_noop(self, stub_encoder_and_output: MagicMock) -> None:
        """Setting a bitrate with no active encoder just stores the new value."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.set_bitrate(camera, 200_000)

        assert manager._bitrate == 200_000  # noqa: SLF001
        camera.stop_encoder.assert_not_called()
        camera.start_encoder.assert_not_called()

    async def test_set_bitrate_while_running_restarts_encoder(
        self,
        stub_encoder_and_output: MagicMock,
    ) -> None:
        """When the encoder is live, set_bitrate should stop+start at the new rate."""
        manager = PreviewPipelineManager()
        camera = MagicMock()
        await manager.acquire(camera)
        old_encoder = camera.start_encoder.call_args.args[0]
        camera.start_encoder.reset_mock()
        camera.stop_encoder.reset_mock()
        stub_encoder_and_output.reset_mock()

        await manager.set_bitrate(camera, 200_000)

        assert manager._bitrate == 200_000  # noqa: SLF001
        camera.stop_encoder.assert_called_once_with(old_encoder)
        camera.start_encoder.assert_called_once()
        # H264Encoder should have been instantiated with the new bitrate.
        assert stub_encoder_and_output.call_args.kwargs.get("bitrate") == 200_000
