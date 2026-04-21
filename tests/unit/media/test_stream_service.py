"""Tests for stream service helpers."""

from typing import cast

import pytest
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.stream import StreamMode

from app.camera.exceptions import YoutubeConfigRequiredError
from app.camera.schemas import YoutubeStreamConfig
from app.media import stream as stream_service
from app.media.mediamtx_client import HIRES_RTSP_URL
from tests.constants import YOUTUBE_EMBED_URL, YOUTUBE_WATCH_URL


class TestStreamUrls:
    """Tests for public-facing URL helpers."""

    def test_broadcast_url(self) -> None:
        """The broadcast URL embeds the broadcast key as the watch ``v`` param."""
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )
        assert stream_service.get_broadcast_url(config) == AnyUrl(YOUTUBE_WATCH_URL)

    def test_embed_url(self) -> None:
        """A public watch URL converts into the matching embed URL."""
        assert stream_service.get_youtube_embed_url(AnyUrl(YOUTUBE_WATCH_URL)) == YOUTUBE_EMBED_URL


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
            stream_service.validate_youtube_mode(cast("StreamMode", _FakeMode()), None)

    def test_valid_youtube_mode_is_noop(self) -> None:
        """Valid (mode, config) pairs return ``None`` without raising."""
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )
        assert stream_service.validate_youtube_mode(StreamMode.YOUTUBE, config) is None

    def test_invalid_stream_key_is_rejected(self) -> None:
        """Stream keys must stay URL-safe."""
        with pytest.raises(ValueError, match="stream key"):
            YoutubeStreamConfig(
                stream_key=SecretStr("bad key!"),
                broadcast_key=SecretStr("broadcast-key"),
            )

    def test_invalid_broadcast_key_is_rejected(self) -> None:
        """Broadcast keys must stay URL-safe too."""
        with pytest.raises(ValueError, match="broadcast key"):
            YoutubeStreamConfig(
                stream_key=SecretStr("stream-key"),
                broadcast_key=SecretStr("bad key!"),
            )


class _FakeFfmpegOutput:
    """Lightweight stand-in for ``picamera2.outputs.FfmpegOutput``.

    The real class is a ``picamera2`` subclass that spawns an ffmpeg
    subprocess on start. For this test we only care about the arguments the
    Pi passes to the constructor — we record them on ``output_filename`` to
    match the real class's attribute name so the assertions stay accurate.
    """

    def __init__(self, output_filename: str) -> None:
        self.output_filename = output_filename


_FFMPEG_FLAG_COPY = "-c:v copy"
_FFMPEG_FLAG_RTSP = "-f rtsp"
_FFMPEG_FLAG_TCP = "-rtsp_transport tcp"
_FFMPEG_ANULLSRC = "anullsrc"
_FFMPEG_RTMPS = "rtmps"


class TestHiresRtspOutput:
    """``build_hires_rtsp_output`` returns an FfmpegOutput pointed at MediaMTX.

    We stub ``FfmpegOutput`` with a recording class so the test runs identically
    on macOS (where picamera2 isn't available) and on a Pi (where it is). The
    goal is to lock in the ffmpeg command shape, which doesn't need real I/O.
    """

    def test_output_targets_mediamtx_cam_hires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The output string publishes to MediaMTX's local RTSP cam-hires path."""
        monkeypatch.setattr(stream_service, "FfmpegOutput", _FakeFfmpegOutput)

        output = cast("_FakeFfmpegOutput", stream_service.build_hires_rtsp_output())

        assert HIRES_RTSP_URL in output.output_filename
        assert _FFMPEG_FLAG_COPY in output.output_filename
        assert _FFMPEG_FLAG_RTSP in output.output_filename
        assert _FFMPEG_FLAG_TCP in output.output_filename
        # No silent-audio hack on the Pi. MediaMTX's ``runOnReady`` owns the
        # YouTube egress (with its own audio track).
        assert _FFMPEG_ANULLSRC not in output.output_filename
        assert _FFMPEG_RTMPS not in output.output_filename
