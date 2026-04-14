"""Tests for stream service helpers."""

from unittest.mock import MagicMock

import pytest
from pydantic import AnyUrl, SecretStr

from relab_rpi_cam_models.stream import StreamMode

from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services import stream as stream_service

EMBED_URL = "https://www.youtube.com/embed/broadcast-key"
RTMPS_BASE = "rtmps://a.rtmps.youtube.com:443/live2"


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
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )

        output = stream_service.get_ffmpeg_output(StreamMode.YOUTUBE, config)

        assert isinstance(output, stream_service.SilentAudioFfmpegOutput)
        assert "-f flv" in output.output_filename
        assert "-shortest" in output.output_filename
        assert f"{RTMPS_BASE}/stream-key" in output.output_filename

    def test_silent_audio_output_orders_inputs_before_codecs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The lavfi audio input must be declared before ffmpeg codec/output options."""
        popen = MagicMock(return_value=MagicMock(stdin=MagicMock()))
        monkeypatch.setattr(stream_service.subprocess, "Popen", popen)
        monkeypatch.setattr(stream_service.Output, "start", MagicMock())

        output = stream_service.SilentAudioFfmpegOutput("-ar 44100 -ac 2 -f flv -shortest rtmps://example")
        output.start()

        command = popen.call_args.args[0]
        anullsrc_index = command.index("anullsrc=channel_layout=stereo:sample_rate=44100")
        video_codec_index = command.index("-c:v")
        audio_codec_index = command.index("-c:a")
        assert anullsrc_index < audio_codec_index < video_codec_index
        assert "nullaudio.monitor" not in command
        # HLS-era leftovers must stay gone.
        assert "hls" not in command
        assert "master.m3u8" not in command
        assert "http_upload_hls" not in command

    def test_unsupported_mode_raises(self) -> None:
        """Unsupported stream modes should fail fast."""

        class _FakeMode:
            def __str__(self) -> str:
                return "fake"

        with pytest.raises(ValueError, match="Unsupported"):
            stream_service.get_ffmpeg_output(_FakeMode())  # type: ignore[arg-type]
