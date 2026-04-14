"""Tests for stream service helpers."""

import pytest
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.stream import StreamMode

from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services import stream as stream_service

EMBED_URL = "https://www.youtube.com/embed/broadcast-key"
RTMPS_BASE = "rtmps://a.rtmps.youtube.com:443/live2"


class DummyFfmpegOutput:
    """Capture FfmpegOutput construction arguments."""

    def __init__(self, output_str: str, **kwargs: object) -> None:
        self.output_str = output_str
        self.kwargs = kwargs


class TestStreamUrls:
    """Tests for stream URL helpers."""

    def test_youtube_rtmps_url_embeds_stream_key(self) -> None:
        """The YouTube RTMPS URL should embed the secret stream key path-style."""
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )
        assert stream_service.build_youtube_rtmps_url(config) == f"{RTMPS_BASE}/stream-key"

    def test_broadcast_url(self) -> None:
        """Test that the broadcast URL is constructed using the broadcast key from the config."""
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )
        assert stream_service.get_broadcast_url(config) == AnyUrl("https://youtube.com/watch?v=broadcast-key")

    def test_embed_url(self) -> None:
        """Test that a watch URL is converted into an embed URL."""
        assert stream_service.get_youtube_embed_url(AnyUrl("https://youtube.com/watch?v=broadcast-key")) == EMBED_URL


class TestFfmpegOutput:
    """Tests for FFmpeg output construction."""

    def test_youtube_output_requires_config(self) -> None:
        """Requesting a YouTube FFmpeg output without config should raise."""
        with pytest.raises(YoutubeConfigRequiredError):
            stream_service.get_ffmpeg_output(StreamMode.YOUTUBE)

    def test_youtube_output_builds_rtmps_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ffmpeg output string should target YouTube RTMPS with FLV muxing and AAC audio."""
        monkeypatch.setattr(stream_service, "FfmpegOutput", DummyFfmpegOutput)
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )

        output = stream_service.get_ffmpeg_output(StreamMode.YOUTUBE, config)

        assert isinstance(output, DummyFfmpegOutput)
        assert output.kwargs["audio"] is True
        assert "-f flv" in output.output_str
        assert "-c:v copy" in output.output_str
        assert "aac" in output.output_str
        assert f"{RTMPS_BASE}/stream-key" in output.output_str
        # HLS-era leftovers must stay gone.
        assert "hls" not in output.output_str
        assert "master.m3u8" not in output.output_str
        assert "http_upload_hls" not in output.output_str

    def test_unsupported_mode_raises(self) -> None:
        """Unsupported stream modes should fail fast."""

        class _FakeMode:
            def __str__(self) -> str:
                return "fake"

        with pytest.raises(ValueError, match="Unsupported"):
            stream_service.get_ffmpeg_output(_FakeMode())  # type: ignore[arg-type]
