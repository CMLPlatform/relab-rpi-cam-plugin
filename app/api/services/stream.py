"""Hardware-dependent business logic for streams."""

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx
from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode, YoutubeConfigRequiredError, YoutubeStreamConfig

from app.api.services.hardware_protocols import FfmpegOutputLike
from app.api.services.hardware_stubs import FfmpegOutputStub

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from picamera2.outputs import FfmpegOutput
else:
    try:
        from picamera2.outputs import FfmpegOutput
    except ImportError:
        FfmpegOutput = FfmpegOutputStub


# YouTube HLS ingestion uses a fixed manifest filename
_YOUTUBE_MANIFEST = "master.m3u8"


def get_ffmpeg_output(mode: StreamMode, youtube_config: YoutubeStreamConfig | None = None) -> FfmpegOutputLike:
    """Create FfmpegOutput object for YouTube HLS ingestion."""
    if mode != StreamMode.YOUTUBE:
        msg = f"Unsupported stream mode: {mode}"
        raise ValueError(msg)

    if not youtube_config:
        raise YoutubeConfigRequiredError

    output_str = (
        "-g 30 -sc_threshold 0 "  # Closed GOP and disabled scene detection
        "-b:v 2500k -maxrate 2500k "  # Limit bitrate to 2500 kb/s
        "-f hls "
        "-hls_time 2 -hls_list_size 5 "  # 5 segments, 2s each
        "-hls_segment_type mpegts "  # MPEG-TS segments
        "-hls_flags delete_segments+independent_segments "
        "-http_persistent 1 "
        "-connect_timeout 10000000 "  # 10 second connection timeout (microseconds)
        "-rw_timeout 30000000 "  # 30 second read/write timeout (microseconds)
        f"-master_pl_name {_YOUTUBE_MANIFEST} "  # Master playlist for YouTube ingestion
        "-method POST "  # Required by YouTube
        f"{get_upload_url(youtube_config)}"  # Upload URL
    )
    return FfmpegOutput(
        output_str,
        audio=True,  # Youtube requires audio
        audio_bitrate=8000,
        # NOTE: Using a PulseAudio null source to avoid feedback. The built-in '-f lavfi -i anullsrc` option
        # is preferred but PiCamera2 FfmpegOutput only accepts pulse devices.
        audio_device="nullaudio.monitor",
    )


def get_upload_url(youtube_config: YoutubeStreamConfig) -> AnyUrl:
    """Get YouTube HLS upload URL."""
    return AnyUrl(
        f"https://a.upload.youtube.com/http_upload_hls?cid={youtube_config.stream_key.get_secret_value()}&copy=0&file={_YOUTUBE_MANIFEST}",
    )


def get_broadcast_url(youtube_config: YoutubeStreamConfig) -> AnyUrl:
    """Get YouTube broadcast URL."""
    return AnyUrl(f"https://youtube.com/watch?v={youtube_config.broadcast_key.get_secret_value()}")


async def validate_stream_key(youtube_config: YoutubeStreamConfig) -> bool:
    """Validate stream key by checking if the upload URL is valid.

    Retries with exponential backoff on network failures or server errors.
    """
    url_str = str(get_upload_url(youtube_config))
    max_retries = 3
    base_delay = 1.0  # seconds

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url_str)
                if response.status_code == 202:
                    return True
                # Don't retry on client errors (4xx), only server errors (5xx)
                if response.status_code < 500:
                    logger.warning("YouTube stream key validation returned %d", response.status_code)
                    return False
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            logger.warning("Stream key validation attempt %d failed: %s", attempt + 1, e)
            if attempt < max_retries - 1:
                delay = base_delay * (2**attempt)  # exponential backoff
                await asyncio.sleep(delay)
                continue
            return False
        except Exception:
            logger.exception("Unexpected error during stream key validation")
            return False

    return False


def get_stream_url(mode: StreamMode, youtube_config: YoutubeStreamConfig | None = None) -> AnyUrl:
    """Get stream URL for a given stream mode."""
    if mode != StreamMode.YOUTUBE:
        msg = f"Unsupported stream mode: {mode}"
        raise ValueError(msg)

    if not youtube_config:
        raise YoutubeConfigRequiredError
    return get_broadcast_url(youtube_config)
