"""Hardware-dependent business logic for streams."""

from urllib.parse import urljoin

import httpx
from picamera2.outputs import FfmpegOutput
from pydantic import AnyUrl

from app.core.config import settings
from relab_rpi_cam_models.stream import StreamMode, YoutubeConfigRequiredError, YoutubeStreamConfig


def get_ffmpeg_output(mode: StreamMode, youtube_config: YoutubeStreamConfig | None = None) -> FfmpegOutput:
    """Create FfmpegOutput object for the given streaming mode."""
    base_output = (
        "-g 30 -sc_threshold 0 "  # Closed GOP and disabled scene detection
        "-b:v 2500k -maxrate 2500k "  # Limit bitrate to 2500 kb/s
        "-f hls "
        "-hls_time 2 -hls_list_size 5 "  # 5 segments, 2s each
        "-hls_segment_type mpegts "  # MPEG-TS segments
        "-hls_flags delete_segments+independent_segments "
        "-http_persistent 1 "
    )

    match mode:
        case StreamMode.YOUTUBE:
            if not youtube_config:
                raise YoutubeConfigRequiredError

            output_str = base_output + (
                f"-master_pl_name {settings.hls_manifest_filename} "  # Create a master playlist
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

        case StreamMode.LOCAL:
            output_str = base_output + str(settings.hls_path / settings.hls_manifest_filename)
            return FfmpegOutput(output_str)


def get_upload_url(youtube_config: YoutubeStreamConfig) -> AnyUrl:
    """Get YouTube HLS upload URL pointing to the stream key."""
    return AnyUrl(
        f"https://a.upload.youtube.com/http_upload_hls?cid={youtube_config.stream_key}&copy=0&file={settings.hls_manifest_filename}"
    )


def get_broadcast_url(youtube_config: YoutubeStreamConfig) -> AnyUrl:
    """Get YouTube broadcast URL."""
    return AnyUrl(f"https://youtube.com/watch?v={youtube_config.broadcast_key}")


async def validate_stream_key(youtube_config: YoutubeStreamConfig) -> bool:
    """Validate stream key by checking if the upload URL is valid."""
    url_str = str(get_upload_url(youtube_config))
    async with httpx.AsyncClient() as client:
        response = await client.post(url_str)
        return response.status_code == 202


def get_stream_url(mode: StreamMode, youtube_config: YoutubeStreamConfig | None = None) -> AnyUrl:
    """Get stream URL for a given stream mode."""
    match mode:
        case StreamMode.YOUTUBE:
            if not youtube_config:
                raise YoutubeConfigRequiredError
            return get_broadcast_url(youtube_config)
        case StreamMode.LOCAL:
            return AnyUrl(urljoin(str(settings.base_url), "/stream/watch"))
