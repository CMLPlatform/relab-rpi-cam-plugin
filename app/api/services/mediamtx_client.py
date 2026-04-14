"""Runtime control plane for the MediaMTX sidecar.

The plugin publishes video into MediaMTX over RTSP and lets MediaMTX handle
everything downstream — LL-HLS for browsers, RTMPS egress for YouTube, and
eventually PeerTube / S3 recording. This module is the thin ``httpx`` wrapper
the plugin uses to patch MediaMTX path configuration at runtime (e.g. to
attach a YouTube RTMPS target to the ``cam-hires`` path when the user starts a
live stream).

MediaMTX exposes its control API on localhost:9997 by default — see
``mediamtx.yml`` ``apiAddress``. It is localhost-only, so we don't need auth
on top. Calls are idempotent: MediaMTX silently overwrites path fields on
patch, and path lookup returns 404 which we swallow.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

# MediaMTX runs in ``network_mode: host``; its control API listens on the
# host loopback, which from inside the app container is reachable via the
# ``host.docker.internal`` bridge. We expose the URL here (not in config) so
# tests can swap it cheaply.
DEFAULT_MEDIAMTX_API_URL = "http://host.docker.internal:9997"
_API_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)

# Hires main stream publish target on the Pi's local MediaMTX (same host
# network as the lores stream, different path). ``stream.py`` builds an
# ``FfmpegOutput`` pointing here; MediaMTX terminates the RTSP publish and
# runs the configured egresses on top.
HIRES_RTSP_URL = "rtsp://host.docker.internal:8554/cam-hires"


class MediaMTXAPIError(RuntimeError):
    """Raised when the MediaMTX control API rejects a request."""


class MediaMTXClient:
    """Thin client for MediaMTX's runtime path patch API."""

    def __init__(self, base_url: str = DEFAULT_MEDIAMTX_API_URL) -> None:
        self._base_url = base_url.rstrip("/")

    async def set_youtube_egress(self, path: str, stream_key: str) -> None:
        """Attach a YouTube RTMPS egress to ``path`` via ``runOnReady``.

        The command runs inside the MediaMTX container (which must use the
        ``-ffmpeg`` image variant) and is terminated automatically when the
        publish stops — no separate teardown call is needed. We still expose
        :meth:`clear_egress` for safety so stop-stream can no-op the patch.

        The ffmpeg invocation mirrors the old ``SilentAudioFfmpegOutput`` from
        ``stream.py`` but lives in the sidecar instead of the Pi:

        - Read H264 from the locally-published RTSP path (``cam-hires``)
        - Synthesize silent stereo audio via lavfi (YouTube rejects video-only
          streams)
        - Copy the video stream (already H264 from picamera2)
        - Encode the synthesized audio as AAC @ 128kbps
        - Mux into FLV and publish to YouTube RTMPS
        """
        youtube_url = f"rtmps://a.rtmps.youtube.com:443/live2/{stream_key}"
        run_on_ready = (
            "ffmpeg -hide_banner -loglevel warning "
            "-rtsp_transport tcp -i rtsp://localhost:8554/$MTX_PATH "
            "-f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 "
            "-c:v copy -c:a aac -b:a 128k "
            f"-f flv -shortest {youtube_url}"
        )
        payload: dict[str, Any] = {
            "runOnReady": run_on_ready,
            "runOnReadyRestart": False,
        }
        await self._patch_path(path, payload)

    async def clear_egress(self, path: str) -> None:
        """Clear any ``runOnReady`` egress on ``path``."""
        await self._patch_path(path, {"runOnReady": "", "runOnReadyRestart": False})

    async def _patch_path(self, path: str, payload: dict[str, Any]) -> None:
        url = f"{self._base_url}/v3/config/paths/patch/{path}"
        try:
            async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
                response = await client.patch(url, json=payload)
        except httpx.HTTPError as exc:
            msg = f"MediaMTX API unreachable at {self._base_url}: {exc}"
            raise MediaMTXAPIError(msg) from exc

        # 200 + empty body is MediaMTX's success response; 404 means the path
        # doesn't exist yet (first start), which is fine because the config
        # file pre-declares ``cam-hires``. Anything else is a real error.
        if response.status_code == 404:
            logger.warning(
                "MediaMTX patch on missing path %r — is it pre-declared in mediamtx.yml?",
                path,
            )
            return
        if response.status_code >= 400:
            body_preview = response.text[:200]
            msg = f"MediaMTX patch on {path!r} failed (HTTP {response.status_code}): {body_preview}"
            raise MediaMTXAPIError(msg)
