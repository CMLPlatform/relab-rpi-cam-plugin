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
import shlex
import shutil
import subprocess
from typing import TYPE_CHECKING, Any, cast

from pydantic import AnyUrl, ValidationError
from relab_rpi_cam_models.stream import StreamMode

from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.hardware_stubs import FfmpegOutputStub

logger = logging.getLogger(__name__)
Output = Any

if TYPE_CHECKING:
    from typing import Any as _Any  # for clarity

    from picamera2.outputs import FfmpegOutput

    Output = _Any
else:
    try:
        from picamera2.outputs import FfmpegOutput
        from picamera2.outputs.output import Output
    except ImportError:
        FfmpegOutput = FfmpegOutputStub
        Output = object


# YouTube RTMPS ingest endpoint. Port 443 is required for RTMPS.
_YOUTUBE_RTMPS_BASE = "rtmps://a.rtmps.youtube.com:443/live2"
_SILENT_AUDIO_SAMPLE_RATE = 44_100
_YOUTUBE_AUDIO_BITRATE = 128_000
_NULL_BYTE = "\x00"


class SilentAudioFfmpegOutput(FfmpegOutput):
    """FfmpegOutput variant that adds a lavfi silent-audio input instead of PulseAudio."""

    def __init__(self, output_filename: str | list[str]) -> None:
        if isinstance(output_filename, str):
            output_args = shlex.split(output_filename)
            output_filename_str = output_filename
        else:
            output_args = list(output_filename)
            output_filename_str = shlex.join(output_args)

        self._output_args = output_args
        super().__init__(output_filename_str, audio=False)
        self.output_filename = output_filename_str
        self.timeout = 1

    def start(self) -> None:
        """Start ffmpeg with video stdin plus a generated silent audio input."""
        ffmpeg_binary = shutil.which("ffmpeg")
        if ffmpeg_binary is None:
            msg = "ffmpeg binary not found in PATH"
            raise RuntimeError(msg)

        general_options = ["-loglevel", "warning", "-y"]
        video_input = [
            "-use_wallclock_as_timestamps",
            "1",
            "-thread_queue_size",
            "64",
            "-i",
            "-",
        ]
        silent_audio_input = [
            "-f",
            "lavfi",
            "-thread_queue_size",
            "1024",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={_SILENT_AUDIO_SAMPLE_RATE}",
        ]
        audio_codec = ["-b:a", str(_YOUTUBE_AUDIO_BITRATE), "-c:a", "aac"]
        video_codec = ["-c:v", "copy"]
        command = [
            ffmpeg_binary,
            *general_options,
            *video_input,
            *silent_audio_input,
            *audio_codec,
            *video_codec,
            *self._output_args,
        ]
        # Runtime sanity checks to guard against accidental or malicious
        # introduction of unsafe argv elements before launching ffmpeg.
        # - Ensure every argv element is a str and contains no NUL bytes.
        # - Re-validate any RTMPS output URL elements using Pydantic.
        for idx, item in enumerate(command):
            if not isinstance(item, str):
                msg = f"ffmpeg argv element at index {idx} is not str"
                raise TypeError(msg)
            if _NULL_BYTE in item:
                msg = "ffmpeg argv contains NUL byte"
                raise ValueError(msg)

        # Re-validate any output URLs (defensive; upstream builders already validate).
        for out in self._output_args:
            if isinstance(out, str) and out.startswith("rtmps://"):
                try:
                    AnyUrl(out)
                except ValidationError as exc:
                    msg = "Invalid RTMPS output URL"
                    raise ValueError(msg) from exc

        # Launch ffmpeg as a list argv (no shell) with stdin piped.
        # The argv elements are validated above (types, NUL bytes, URLs).
        self.ffmpeg = subprocess.Popen(  # noqa: S603 - validated argv
            command,
            stdin=subprocess.PIPE,
            process_group=0,
        )
        cast("Any", Output).start(self)


def build_youtube_rtmps_url(youtube_config: YoutubeStreamConfig) -> str:
    """Return the RTMPS URL the Pi publishes its main-stream H264 to.

    Validate the assembled RTMPS URL using Pydantic's `AnyUrl` so invalid
    or malicious stream keys are rejected before being handed to ffmpeg.
    """
    url = f"{_YOUTUBE_RTMPS_BASE}/{youtube_config.stream_key.get_secret_value()}"
    try:
        validated = AnyUrl(url)
    except ValidationError as exc:
        msg = "Invalid YouTube stream key or URL"
        raise ValueError(msg) from exc
    return str(validated)


def get_ffmpeg_output(mode: StreamMode, youtube_config: YoutubeStreamConfig | None = None) -> object:
    """Build an ``FfmpegOutput`` that pipes the main H264 encoder to YouTube RTMPS.

    Audio is required by YouTube. We synthesize silence with ffmpeg's lavfi
    ``anullsrc`` input so no runtime audio device or PulseAudio sink is needed.
    """
    if mode != StreamMode.YOUTUBE:
        msg = f"Unsupported stream mode: {mode}"
        raise ValueError(msg)

    if not youtube_config:
        raise YoutubeConfigRequiredError

    # Picamera2's FfmpegOutput always supplies the H264 stdin as input 0 before
    # appending this string. We add an infinite silent audio input as input 1,
    # then use ``-shortest`` so the ffmpeg process exits when video stops.
    output_args = [
        "-ar",
        str(_SILENT_AUDIO_SAMPLE_RATE),
        "-ac",
        "2",
        "-f",
        "flv",
        "-shortest",
        build_youtube_rtmps_url(youtube_config),
    ]
    return SilentAudioFfmpegOutput(output_args)


def get_broadcast_url(youtube_config: YoutubeStreamConfig) -> AnyUrl:
    """Get YouTube broadcast URL."""
    return AnyUrl(f"https://youtube.com/watch?v={youtube_config.broadcast_key.get_secret_value()}")


def get_youtube_embed_url(broadcast_url: AnyUrl) -> str:
    """Convert a public YouTube watch URL into an embeddable URL."""
    url_str = str(broadcast_url)
    return url_str.replace("https://youtube.com/watch?v=", "https://www.youtube.com/embed/", 1)
