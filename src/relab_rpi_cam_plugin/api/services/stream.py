"""Hardware-dependent business logic for streams."""

from picamera2.outputs import FfmpegOutput

from relab_rpi_cam_plugin.api.models.stream import StreamMode, YoutubeConfigRequiredError, YoutubeStreamConfig
from relab_rpi_cam_plugin.core.config import settings


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
                f"{youtube_config.get_upload_url()}"  # Upload URL
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
