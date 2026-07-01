"""Tests for the lores preview pipeline manager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.camera.streaming import preview_pipeline as preview_pipeline_mod
from app.camera.streaming.preview_pipeline import PreviewPipelineManager


@pytest.fixture(autouse=True)
def stub_encoder_and_output(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace H264Encoder + the RTSP output builder with MagicMocks so start_encoder is safe."""
    encoder_cls = MagicMock(side_effect=lambda *_a, **_k: MagicMock(name="H264Encoder-instance"))
    output_factory = MagicMock(return_value=MagicMock(name="FfmpegOutput-instance"))
    monkeypatch.setattr(preview_pipeline_mod, "H264Encoder", encoder_cls)
    monkeypatch.setattr(preview_pipeline_mod, "build_rtsp_ffmpeg_output", output_factory)
    return encoder_cls


@pytest.fixture
def backend() -> MagicMock:
    """A mock CameraBackend with async encoder methods."""
    b = MagicMock()
    b.start_lores_encoder = AsyncMock()
    b.stop_lores_encoder = AsyncMock()
    return b


class TestStartStop:
    """Always-on lifecycle: idempotent start, clean stop."""

    async def test_start_delegates_to_backend(self, backend: MagicMock, stub_encoder_and_output: MagicMock) -> None:
        """``start`` calls backend.start_lores_encoder with encoder and output."""
        manager = PreviewPipelineManager()

        await manager.start(backend)

        assert manager.is_running
        backend.start_lores_encoder.assert_awaited_once()
        stub_encoder_and_output.assert_called_once()

    async def test_start_is_idempotent(self, backend: MagicMock) -> None:
        """Calling start twice must not restart the encoder."""
        manager = PreviewPipelineManager()

        await manager.start(backend)
        await manager.start(backend)

        assert manager.is_running
        backend.start_lores_encoder.assert_awaited_once()

    async def test_stop_delegates_to_backend(self, backend: MagicMock) -> None:
        """``stop`` calls backend.stop_lores_encoder with the encoder instance."""
        manager = PreviewPipelineManager()

        await manager.start(backend)
        await manager.stop(backend)

        assert not manager.is_running
        backend.stop_lores_encoder.assert_awaited_once()

    async def test_stop_without_start_is_noop(self, backend: MagicMock) -> None:
        """Stopping an idle manager must not error or touch the backend."""
        manager = PreviewPipelineManager()

        await manager.stop(backend)

        assert not manager.is_running
        backend.stop_lores_encoder.assert_not_awaited()

    async def test_start_propagates_backend_error(self, backend: MagicMock) -> None:
        """A startup error raised by the backend surfaces to the caller."""
        manager = PreviewPipelineManager()
        backend.start_lores_encoder.side_effect = RuntimeError("startup timeout")

        with pytest.raises(RuntimeError, match="startup timeout"):
            await manager.start(backend)

    async def test_stop_swallows_backend_error(self, backend: MagicMock) -> None:
        """A stop error raised by the backend is swallowed (encoder is dead anyway)."""
        manager = PreviewPipelineManager()

        await manager.start(backend)
        backend.stop_lores_encoder.side_effect = RuntimeError("already dead")

        await manager.stop(backend)

        assert not manager.is_running


class TestSetBitrate:
    """Thermal governor support: set_bitrate reconfigures the live encoder."""

    async def test_set_bitrate_while_idle_is_noop(self, backend: MagicMock) -> None:
        """Setting a bitrate with no active encoder just stores the new value."""
        manager = PreviewPipelineManager()

        await manager.set_bitrate(backend, 200_000)

        assert manager._bitrate == 200_000
        backend.stop_lores_encoder.assert_not_awaited()
        backend.start_lores_encoder.assert_not_awaited()

    async def test_set_bitrate_while_running_restarts_encoder(
        self,
        backend: MagicMock,
        stub_encoder_and_output: MagicMock,
    ) -> None:
        """When the encoder is live, set_bitrate should stop+start at the new rate."""
        manager = PreviewPipelineManager()
        await manager.start(backend)
        first_encoder = backend.start_lores_encoder.await_args.args[0]
        backend.start_lores_encoder.reset_mock()
        backend.stop_lores_encoder.reset_mock()
        stub_encoder_and_output.reset_mock()

        await manager.set_bitrate(backend, 200_000)

        assert manager._bitrate == 200_000
        backend.stop_lores_encoder.assert_awaited_once()
        backend.start_lores_encoder.assert_awaited_once()
        # A fresh encoder (not the original) must be handed to the backend at the new rate.
        new_encoder = backend.start_lores_encoder.await_args.args[0]
        assert new_encoder is not first_encoder
        assert new_encoder.bitrate == 200_000
