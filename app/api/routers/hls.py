"""LL-HLS proxy from the Pi API to the local MediaMTX sidecar.

MediaMTX serves the LL-HLS playlist + segments on ``:8888`` under
``/{path}/index.m3u8`` and friends. The Pi's FastAPI app doesn't listen on
that port — and even if it did, the browser couldn't reach it across the
relay WAN hop. Instead, the backend proxies segment fetches through the
WebSocket relay, and the relay terminates on this router, which re-issues
the HTTP GET against MediaMTX on the host network.

Route shape: ``GET /hls/{hls_path:path}`` where ``hls_path`` is the full
sub-path MediaMTX expects — e.g. ``cam-preview/index.m3u8`` or
``cam-preview/segment0.mp4``. The m3u8 playlist parser at the browser
resolves segment references relative to the playlist URL, so every
downstream request comes back through this same route.

Binary content types (``video/*``) are returned verbatim; the m3u8 playlist
is returned as text. The relay client in ``app/utils/relay.py`` flags
``video/*`` responses as binary and sends them over the WebSocket as a
header-frame + binary-frame pair.
"""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Response
from fastapi import Path as FastAPIPath

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hls", tags=["hls"])

# MediaMTX LL-HLS listener. Runs on the host network so the app container
# reaches it via the docker host-gateway alias.
_MEDIAMTX_HLS_BASE = "http://host.docker.internal:8888"
_HLS_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)


@router.get(
    "/{hls_path:path}",
    summary="Proxy an LL-HLS playlist or segment from MediaMTX",
    responses={
        200: {"description": "Playlist (``application/vnd.apple.mpegurl``) or segment (``video/mp4``)."},
        404: {"description": "Stream not yet published — preview encoder hasn't started."},
        503: {"description": "MediaMTX unreachable."},
    },
)
async def proxy_hls(
    hls_path: Annotated[
        str,
        FastAPIPath(
            description="MediaMTX-relative path, e.g. ``cam-preview/index.m3u8``",
            pattern=r"^[a-zA-Z0-9_\-/\.]+$",
        ),
    ],
) -> Response:
    """Fetch an LL-HLS resource from the local MediaMTX and return it verbatim."""
    if any(segment in (".", "..") for segment in hls_path.split("/")):
        raise HTTPException(status_code=400, detail="Invalid HLS path")
    target_url = f"{_MEDIAMTX_HLS_BASE}/{hls_path}"
    try:
        async with httpx.AsyncClient(timeout=_HLS_TIMEOUT) as client:
            response = await client.get(target_url)
    except httpx.HTTPError as exc:
        logger.warning("MediaMTX HLS unreachable: %s", exc)
        raise HTTPException(status_code=503, detail=f"MediaMTX HLS unreachable: {exc}") from exc

    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail=(
                "HLS path not found — the preview encoder may not be running yet. "
                "Wait ~2s for MediaMTX to see the first publish and retry."
            ),
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"MediaMTX rejected HLS request (HTTP {response.status_code})",
        )

    # Pass through the MediaMTX content-type so the relay's binary detection
    # fires correctly on ``video/*`` responses.
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type"),
        headers={"Cache-Control": "no-store"},
    )
