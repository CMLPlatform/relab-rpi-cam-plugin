"""Tests for stream service helpers."""

import pytest
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.stream import StreamMode

from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services import stream as stream_service
from app.api.services.mediamtx_client import HIRES_RTSP_URL

EMBED_URL = "https://www.youtube.com/embed/broadcast-key"


class TestStreamUrls:
    """Tests for public-facing URL helpers."""

    def test_broadcast_url(self) -> None:
        """The broadcast URL embeds the broadcast key as the watch ``v`` param."""
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )
        assert stream_service.get_broadcast_url(config) == AnyUrl("https://youtube.com/watch?v=broadcast-key")

    def test_embed_url(self) -> None:
        """A public watch URL converts into the matching embed URL."""
        assert (
            stream_service.get_youtube_embed_url(AnyUrl("https://youtube.com/watch?v=broadcast-key"))
            == EMBED_URL
        )


class TestValidateYoutubeMode:
    """``validate_youtube_mode`` is the fail-fast guard at the top of ``start_stream``."""

    def test_youtube_mode_requires_config(self) -> None:
        """YouTube mode without a config raises the dedicated error."""
        with pytest.raises(YoutubeConfigRequiredError):
            stream_service.validate_youtube_mode(StreamMode.YOUTUBE, None)

    def test_unsupported_mode_raises(self) -> None:
        """Non-YouTube modes raise ``ValueError`` with an 'Unsupported' message."""

        class _FakeMode:
            def __str__(self) -> str:
                return "fake"

        with pytest.raises(ValueError, match="Unsupported"):
            stream_service.validate_youtube_mode(_FakeMode(), None)  # type: ignore[arg-type]

    def test_valid_youtube_mode_is_noop(self) -> None:
        """Valid (mode, config) pairs return ``None`` without raising."""
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )
        assert stream_service.validate_youtube_mode(StreamMode.YOUTUBE, config) is None


class TestHiresRtspOutput:
    """``build_hires_rtsp_output`` returns an FfmpegOutput pointed at MediaMTX."""

    def test_output_targets_mediamtx_cam_hires(self) -> None:
        """The output string publishes to MediaMTX's local RTSP cam-hires path."""
        pytest.importorskip("picamera2.outputs")

        output = stream_service.build_hires_rtsp_output()

        assert HIRES_RTSP_URL in output.output_filename
        assert "-c:v copy" in output.output_filename
        assert "-f rtsp" in output.output_filename
        assert "-rtsp_transport tcp" in output.output_filename
        # Post-Phase-9: no more silent-audio hack on the Pi. MediaMTX's
        # ``runOnReady`` owns the YouTube egress (with its own audio track).
        assert "anullsrc" not in output.output_filename
        assert "rtmps" not in output.output_filename
