"""Hardware-dependent business logic for live streams.

The Pi's main H264 encoder publishes to the local MediaMTX sidecar over RTSP,
and MediaMTX handles the YouTube RTMPS egress via a ``runOnReady`` ffmpeg
(configured at runtime by ``mediamtx_client``). The Pi doesn't own an
outbound ffmpeg subprocess of its own — no silent-audio hack, no ``prctl``
PDEATHSIG dance, no custom subprocess supervision. Every streaming
destination the plugin grows in future (PeerTube, S3 archive, local
recording) becomes a MediaMTX path patch with zero Pi-side changes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode

from app.camera.exceptions import YoutubeConfigRequiredError
from app.camera.schemas import YoutubeStreamConfig
from app.camera.services.hardware_stubs import FfmpegOutputStub
from app.media.mediamtx_client import HIRES_RTSP_URL

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from picamera2.outputs import FfmpegOutput
else:
    try:
        from picamera2.outputs import FfmpegOutput
    except ImportError:
        FfmpegOutput = FfmpegOutputStub

_DEFAULT_RTSP_PKT_SIZE = 1400


def build_hires_rtsp_output() -> object:
    """Build the ``FfmpegOutput`` that publishes the main H264 encoder to MediaMTX.

    Picamera2's ``FfmpegOutput`` runs a small ffmpeg subprocess that muxes the
    H264 stdin stream into the given output. Here the output is RTSP to the
    local MediaMTX ``cam-hires`` path — so the subprocess is a tiny H264→RTSP
    remuxer with no filters, no audio, no re-encoding. MediaMTX handles the
    rest (LL-HLS preview, YouTube egress, anything else).
    """
    return FfmpegOutput(f"-f rtsp -rtsp_transport tcp -pkt_size {_DEFAULT_RTSP_PKT_SIZE} {HIRES_RTSP_URL}")


def validate_youtube_mode(mode: StreamMode, youtube_config: YoutubeStreamConfig | None) -> None:
    """Require a YouTube config when starting a YouTube stream. Fail-fast."""
    if mode != StreamMode.YOUTUBE:
        msg = f"Unsupported stream mode: {mode}"
        raise ValueError(msg)
    if not youtube_config:
        raise YoutubeConfigRequiredError


def get_broadcast_url(youtube_config: YoutubeStreamConfig) -> AnyUrl:
    """Get YouTube broadcast URL."""
    return AnyUrl(f"https://youtube.com/watch?v={youtube_config.broadcast_key.get_secret_value()}")


def get_youtube_embed_url(broadcast_url: AnyUrl) -> str:
    """Convert a public YouTube watch URL into an embeddable URL."""
    url_str = str(broadcast_url)
    return url_str.replace("https://youtube.com/watch?v=", "https://www.youtube.com/embed/", 1)
