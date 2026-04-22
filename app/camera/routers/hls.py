"""Preview routes for the Pi API and the local MediaMTX sidecar.

MediaMTX serves the LL-HLS playlist + segments on ``:8888`` under
``/{path}/index.m3u8`` and friends. The Pi's FastAPI app doesn't listen on
that port — and even if it did, the browser couldn't reach it across the
relay WAN hop. Instead, the backend proxies segment fetches through the
WebSocket relay, and the relay terminates on this router, which re-issues
the HTTP GET against MediaMTX on the host network.

Route shape: ``GET /preview/hls/{hls_path:path}`` where ``hls_path`` is the full
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
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi import Path as FastAPIPath
from pydantic import AfterValidator

from app.camera.dependencies import CameraManagerDependency
from app.camera.services.manager import CameraManager
from app.core.runtime import get_request_runtime
from app.media.preview_pipeline import PreviewPipelineManager
from app.observability.logging import build_log_extra
from app.relay.state import RelayRuntimeState
from app.utils.network import is_local_client
from app.workers.preview_thumbnail import PreviewThumbnailWorker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/preview", tags=["preview"])


def _no_traversal(v: str) -> str:
    """Reject path segments that would navigate outside the MediaMTX root."""
    if any(seg in (".", "..") for seg in v.split("/")):
        msg = "Path traversal not allowed"
        raise ValueError(msg)
    return v


# MediaMTX LL-HLS listener. Both services run on the host network so this
# is a plain loopback address.
_MEDIAMTX_HLS_BASE = "http://localhost:8888"
_HLS_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)
_PREVIEW_HLS_PREFIX = "cam-preview/"


def get_preview_pipeline(request: Request) -> PreviewPipelineManager:
    """Resolve the preview pipeline from the request runtime."""
    return get_request_runtime(request).preview_pipeline


def get_relay_state(request: Request) -> RelayRuntimeState:
    """Resolve relay activity state from the request runtime."""
    return get_request_runtime(request).relay_state


def get_preview_thumbnail_worker(request: Request) -> PreviewThumbnailWorker:
    """Resolve the preview thumbnail worker from the request runtime."""
    return get_request_runtime(request).preview_thumbnail_worker


PreviewPipelineDependency = Annotated[PreviewPipelineManager, Depends(get_preview_pipeline)]
RelayStateDependency = Annotated[RelayRuntimeState, Depends(get_relay_state)]
PreviewThumbnailWorkerDependency = Annotated[PreviewThumbnailWorker, Depends(get_preview_thumbnail_worker)]


def _is_local_client(host: str | None) -> bool:
    """Return whether an unauthenticated HLS request came from a local network."""
    return is_local_client(host)


async def _wake_preview_encoder(
    *,
    hls_path: str,
    camera_manager: CameraManager,
    pipeline: PreviewPipelineManager,
) -> None:
    """Best-effort wake for the app-managed local preview path."""
    if not hls_path.startswith(_PREVIEW_HLS_PREFIX) or pipeline.is_running:
        return

    camera = camera_manager.backend.camera
    if camera is None:
        return

    try:
        await pipeline.start(camera)
    except RuntimeError as exc:
        # Leave the response path to report MediaMTX's current state. The next
        # playlist poll will retry after the sleeper has seen the HLS activity.
        logger.warning("Failed to wake preview encoder for HLS request: %s", exc, extra=build_log_extra())


@router.post(
    "/start",
    summary="Start the preview encoder and RTSP publish to MediaMTX",
    responses={
        204: {"description": "Encoder started (or was already running)."},
        503: {"description": "Camera not ready or encoder failed to start."},
    },
    status_code=204,
)
async def start_preview(
    request: Request,
    camera_manager: CameraManagerDependency,
    pipeline: PreviewPipelineDependency,
) -> Response:
    """Explicitly start the preview pipeline. Idempotent."""
    if not _is_local_client(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="Preview control is only available from the local network")
    camera = camera_manager.backend.camera
    if camera is None:
        raise HTTPException(status_code=503, detail="Camera not ready")
    try:
        await pipeline.start(camera)
    except RuntimeError as exc:
        logger.warning("Failed to start preview encoder: %s", exc, extra=build_log_extra())
        raise HTTPException(status_code=503, detail=f"Failed to start preview encoder: {exc}") from exc
    return Response(status_code=204)


@router.post(
    "/stop",
    summary="Stop the preview encoder and refresh the cached thumbnail",
    responses={204: {"description": "Encoder stopped (or was already idle)."}},
    status_code=204,
)
async def stop_preview(
    request: Request,
    camera_manager: CameraManagerDependency,
    pipeline: PreviewPipelineDependency,
    thumbnail_worker: PreviewThumbnailWorkerDependency,
) -> Response:
    """Explicitly stop the preview pipeline and refresh the cached thumbnail."""
    if not _is_local_client(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="Preview control is only available from the local network")
    camera = camera_manager.backend.camera
    if camera is not None and pipeline.is_running:
        try:
            await pipeline.stop(camera)
        except RuntimeError as exc:
            logger.warning("Failed to stop preview encoder: %s", exc, extra=build_log_extra())
    await thumbnail_worker.refresh_once(reason="preview-stop", upload=False)
    return Response(status_code=204)


@router.get(
    "/hls/{hls_path:path}",
    summary="Proxy an LL-HLS playlist or segment from MediaMTX",
    responses={
        200: {"description": "Playlist (``application/vnd.apple.mpegurl``) or segment (``video/mp4``)."},
        404: {"description": "Stream not yet published — preview encoder hasn't started."},
        503: {"description": "MediaMTX unreachable."},
    },
)
async def proxy_hls(
    request: Request,
    hls_path: Annotated[
        str,
        FastAPIPath(
            description="MediaMTX-relative path, e.g. ``cam-preview/index.m3u8``",
            pattern=r"^[a-zA-Z0-9_\-/\.]+$",
        ),
        AfterValidator(_no_traversal),
    ],
    camera_manager: CameraManagerDependency,
    pipeline: PreviewPipelineDependency,
    relay_state: RelayStateDependency,
) -> Response:
    """Fetch an LL-HLS resource from the local MediaMTX and return it verbatim."""
    if not _is_local_client(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="HLS preview is only available from the local network")

    # Record viewer intent before hitting MediaMTX. If the encoder is asleep,
    # the first playlist request is exactly the signal that should wake it.
    relay_state.mark_hls_activity()
    await _wake_preview_encoder(hls_path=hls_path, camera_manager=camera_manager, pipeline=pipeline)

    # Confine user input to the path component only — scheme and host come from
    # the trusted constant, preventing any influence on the request destination.
    target_url = httpx.URL(_MEDIAMTX_HLS_BASE).copy_with(path=f"/{hls_path}")
    try:
        async with httpx.AsyncClient(timeout=_HLS_TIMEOUT) as client:
            response = await client.get(target_url)
    except httpx.HTTPError as exc:
        logger.warning("MediaMTX HLS unreachable: %s", exc, extra=build_log_extra())
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
