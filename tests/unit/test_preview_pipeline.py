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


@pytest.fixture(autouse=True)
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


class TestStartStop:
    """Always-on lifecycle: idempotent start, clean stop."""

    async def test_start_attaches_encoder_to_lores_stream(self, stub_encoder_and_output: MagicMock) -> None:
        """``start`` attaches a fresh H264Encoder to the lores picamera2 stream."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.start(camera)

        assert manager.is_running
        camera.start_encoder.assert_called_once()
        assert camera.start_encoder.call_args.kwargs == {"name": "lores"}
        stub_encoder_and_output.assert_called_once()

    async def test_start_is_idempotent(self) -> None:
        """Calling start twice must not restart the encoder."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.start(camera)
        await manager.start(camera)

        assert manager.is_running
        camera.start_encoder.assert_called_once()

    async def test_stop_detaches_the_encoder(self) -> None:
        """``stop`` detaches the encoder instance it previously attached."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.start(camera)
        encoder = camera.start_encoder.call_args.args[0]
        await manager.stop(camera)

        assert not manager.is_running
        camera.stop_encoder.assert_called_once_with(encoder)

    async def test_stop_without_start_is_noop(self) -> None:
        """Stopping an idle manager must not error or touch the camera."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.stop(camera)

        assert not manager.is_running
        camera.stop_encoder.assert_not_called()


class TestSetBitrate:
    """Thermal governor support: set_bitrate reconfigures the live encoder."""

    async def test_set_bitrate_while_idle_is_noop(self) -> None:
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
        await manager.start(camera)
        old_encoder = camera.start_encoder.call_args.args[0]
        camera.start_encoder.reset_mock()
        camera.stop_encoder.reset_mock()
        stub_encoder_and_output.reset_mock()

        await manager.set_bitrate(camera, 200_000)

        assert manager._bitrate == 200_000  # noqa: SLF001
        camera.stop_encoder.assert_called_once_with(old_encoder)
        camera.start_encoder.assert_called_once()
        # The new encoder should have its bitrate attribute set to the new value.
        new_encoder = stub_encoder_and_output.return_value
        assert new_encoder.bitrate == 200_000
