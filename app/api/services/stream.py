"""Hardware-dependent business logic for YouTube live streams.

Phase 6B: HLS-over-HTTP ingest is gone. YouTube now accepts RTMPS at
``rtmps://a.rtmps.youtube.com:443/live2/{stream_key}`` and the Pi's
``Picamera2`` H264 encoder pipes directly to an ``FfmpegOutput`` that
publishes there. No intermediate MediaMTX hop for YouTube — the app owns the
ffmpeg subprocess lifecycle directly so start/stop is crisp.

Stream-key validation used to ping YouTube's HLS endpoint to check the key
before opening the real stream. With HLS gone and no light-weight RTMPS probe
available, we simply trust the key at start and let YouTube reject at connect
time — the error surfaces as a clean RuntimeError from ``start_recording``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode

from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.hardware_stubs import FfmpegOutputStub

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from picamera2.outputs import FfmpegOutput
else:
    try:
        from picamera2.outputs import FfmpegOutput
    except ImportError:
        FfmpegOutput = FfmpegOutputStub


# YouTube RTMPS ingest endpoint. Port 443 is required for RTMPS.
_YOUTUBE_RTMPS_BASE = "rtmps://a.rtmps.youtube.com:443/live2"


def build_youtube_rtmps_url(youtube_config: YoutubeStreamConfig) -> str:
    """Return the RTMPS URL the Pi publishes its main-stream H264 to."""
    return f"{_YOUTUBE_RTMPS_BASE}/{youtube_config.stream_key.get_secret_value()}"


def get_ffmpeg_output(mode: StreamMode, youtube_config: YoutubeStreamConfig | None = None) -> object:
    """Build an ``FfmpegOutput`` that pipes the main H264 encoder to YouTube RTMPS.

    Audio is required by YouTube. We source silence from the container's null
    PulseAudio sink (created by ``docker_entrypoint.sh``) so there's a valid
    AAC track alongside the video copy.
    """
    if mode != StreamMode.YOUTUBE:
        msg = f"Unsupported stream mode: {mode}"
        raise ValueError(msg)

    if not youtube_config:
        raise YoutubeConfigRequiredError

    # ``-c:v copy`` tells ffmpeg to re-mux picamera2's H264 without re-encoding,
    # ``-f flv`` is the required container for RTMP/RTMPS, ``-shortest`` stops
    # when the video ends so the null-audio track doesn't keep the pipeline
    # alive after stop_stream.
    output_str = (
        "-c:v copy "
        "-c:a aac -b:a 128k -ar 44100 -ac 2 "
        "-f flv "
        "-shortest "
        f"{build_youtube_rtmps_url(youtube_config)}"
    )
    return FfmpegOutput(
        output_str,
        audio=True,
        audio_bitrate=128_000,
        audio_device="nullaudio.monitor",
    )


def get_broadcast_url(youtube_config: YoutubeStreamConfig) -> AnyUrl:
    """Get YouTube broadcast URL."""
    return AnyUrl(f"https://youtube.com/watch?v={youtube_config.broadcast_key.get_secret_value()}")


def get_youtube_embed_url(broadcast_url: AnyUrl) -> str:
    """Convert a public YouTube watch URL into an embeddable URL."""
    url_str = str(broadcast_url)
    return url_str.replace("https://youtube.com/watch?v=", "https://www.youtube.com/embed/", 1)
