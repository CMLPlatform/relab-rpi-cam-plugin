"""Tests for the lores preview pipeline manager."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.media import preview_pipeline as preview_pipeline_mod
from app.media.preview_pipeline import PreviewPipelineManager


@pytest.fixture(autouse=True)
def stub_encoder_and_output(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace H264Encoder + FfmpegOutput with MagicMocks so start_encoder is safe."""
    encoder_cls = MagicMock(return_value=MagicMock(name="H264Encoder-instance"))
    output_factory = MagicMock(return_value=MagicMock(name="FfmpegOutput-instance"))
    monkeypatch.setattr(preview_pipeline_mod, "H264Encoder", encoder_cls)
    monkeypatch.setattr(preview_pipeline_mod, "_build_ffmpeg_output", output_factory)
    return encoder_cls


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

    async def test_start_timeout_is_wrapped_as_runtime_error(self) -> None:
        """A slow encoder start should surface as a startup timeout."""
        manager = PreviewPipelineManager()
        camera = MagicMock()
        camera.start_encoder.side_effect = TimeoutError

        with pytest.raises(RuntimeError, match="startup timeout"):
            await manager.start(camera)

    async def test_stop_runtime_error_is_swallowed(self) -> None:
        """A stop failure should be logged and cleared, not propagated."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.start(camera)
        camera.stop_encoder.side_effect = RuntimeError("already dead")

        await manager.stop(camera)

        assert not manager.is_running


class TestSetBitrate:
    """Thermal governor support: set_bitrate reconfigures the live encoder."""

    async def test_set_bitrate_while_idle_is_noop(self) -> None:
        """Setting a bitrate with no active encoder just stores the new value."""
        manager = PreviewPipelineManager()
        camera = MagicMock()

        await manager.set_bitrate(camera, 200_000)

        assert manager._bitrate == 200_000
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

        assert manager._bitrate == 200_000
        camera.stop_encoder.assert_called_once_with(old_encoder)
        camera.start_encoder.assert_called_once()
        # The new encoder should have its bitrate attribute set to the new value.
        new_encoder = stub_encoder_and_output.return_value
        assert new_encoder.bitrate == 200_000
