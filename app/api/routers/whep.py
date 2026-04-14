"""WHEP (WebRTC HTTP Egress) proxy router.

Browsers can't reach MediaMTX's WHEP endpoint directly through the backend
relay because the relay only carries JSON payloads. This router translates
between the relay's JSON shape and MediaMTX's raw SDP shape:

    Browser/Backend relay  ─▶  POST /whep    {"sdp": "v=0..."}
                                    │
                                    │ forwards raw SDP
                                    ▼
                          mediamtx:8889/cam-preview/whep   (raw text/plain; application/sdp)
                                    │
                                    │ answer SDP + Location header
                                    ▼
    Browser/Backend relay  ◀── {"sdp": "v=0 answer", "session_id": "<uuid>"}

    ...later...

    DELETE /whep/{session_id}  ─▶  DELETE mediamtx session location

The session_id is an opaque UUID the Pi generates; internally it maps to the
``Location`` header returned by MediaMTX so we don't leak implementation
paths to the backend/browser. The Pi also reference-counts the lores preview
pipeline via ``PreviewPipelineManager``: the first WHEP session turns the
encoder on, the last DELETE turns it off.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Body, HTTPException, Path
from pydantic import BaseModel, Field

from relab_rpi_cam_models.camera import CameraMode

from app.api.dependencies.camera_management import CameraManagerDependency
from app.api.exceptions import CameraInitializationError
from app.api.services.preview_pipeline import (
    PreviewPipelineManager,
    get_preview_pipeline_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whep", tags=["whep"])

# MediaMTX's WHEP path matches the RTSP path name. `cam-preview` is the lores
# preview ingest (see ``preview_pipeline.DEFAULT_MEDIAMTX_URL``).
_MEDIAMTX_WHEP_URL = "http://host.docker.internal:8889/cam-preview/whep"
_WHEP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


class WhepOfferRequest(BaseModel):
    """JSON envelope for an incoming WHEP offer."""

    sdp: str = Field(description="Raw SDP offer from the browser.")


class WhepAnswerResponse(BaseModel):
    """JSON envelope for an outgoing WHEP answer + opaque session id."""

    sdp: str = Field(description="Raw SDP answer from MediaMTX.")
    session_id: str = Field(description="Opaque handle used to tear the session down.")


# In-memory mapping: session_id -> MediaMTX resource Location (absolute URL).
# Small and process-local — we don't have many concurrent previews on a single
# Pi, and the data is only meaningful for the lifetime of the WebRTC connection.
_sessions: dict[str, str] = {}


@router.post(
    "",
    summary="Open a WHEP preview session",
    responses={
        200: {"description": "WebRTC answer + session handle."},
        502: {"description": "MediaMTX rejected the offer or returned a malformed response."},
        503: {"description": "MediaMTX or the preview pipeline could not be started."},
    },
)
async def open_whep_session(
    camera_manager: CameraManagerDependency,
    offer: Annotated[WhepOfferRequest, Body(description="SDP offer from the browser")],
) -> WhepAnswerResponse:
    """Forward a browser WHEP offer to the local MediaMTX and return its answer."""
    pipeline = get_preview_pipeline_manager()
    camera = await _ensure_camera(camera_manager)

    try:
        await pipeline.acquire(camera)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"Preview pipeline unavailable: {exc}") from exc

    try:
        answer_sdp, location = await _post_offer_to_mediamtx(offer.sdp)
    except Exception:
        # Roll back the pipeline refcount on any failure so we don't leak encoders.
        await pipeline.release(camera)
        raise

    session_id = uuid.uuid4().hex
    _sessions[session_id] = location
    logger.info("Opened WHEP session %s (subscribers=%d)", session_id, pipeline.active_subscribers)
    return WhepAnswerResponse(sdp=answer_sdp, session_id=session_id)


@router.delete(
    "/{session_id}",
    status_code=204,
    summary="Close a WHEP preview session",
    responses={
        204: {"description": "Session closed."},
        404: {"description": "Unknown session id."},
    },
)
async def close_whep_session(
    camera_manager: CameraManagerDependency,
    session_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
) -> None:
    """Tear down a WHEP session and decrement the preview pipeline ref count."""
    location = _sessions.pop(session_id, None)
    if location is None:
        raise HTTPException(status_code=404, detail=f"Unknown WHEP session: {session_id}")

    pipeline = get_preview_pipeline_manager()
    camera = await _ensure_camera(camera_manager)

    try:
        await _delete_mediamtx_session(location)
    finally:
        # Always release the pipeline so a failed DELETE on MediaMTX doesn't
        # leak the encoder forever.
        await pipeline.release(camera)

    logger.info("Closed WHEP session %s (subscribers=%d)", session_id, pipeline.active_subscribers)


async def _ensure_camera(camera_manager: Any) -> Any:
    """Lazily prime the persistent pipeline and return the Picamera2 handle.

    WHEP sessions are usually the first thing the browser hits after page load,
    so the camera is typically still cold (or the ``camera_to_standby`` recurring
    task has cleaned it up between sessions). ``setup_camera`` is idempotent, so
    calling it here just starts the backend on the first session and no-ops on
    subsequent ones.
    """
    try:
        await camera_manager.setup_camera(CameraMode.VIDEO)
    except CameraInitializationError as exc:
        raise HTTPException(status_code=503, detail=f"Camera backend unavailable: {exc}") from exc
    backend = camera_manager.backend
    camera = backend._camera  # noqa: SLF001
    if camera is None:
        raise HTTPException(status_code=503, detail="Camera backend failed to initialise")
    return camera


async def _post_offer_to_mediamtx(sdp_offer: str) -> tuple[str, str]:
    """POST a raw SDP offer to MediaMTX's WHEP endpoint. Return (answer, location)."""
    try:
        async with httpx.AsyncClient(timeout=_WHEP_TIMEOUT) as client:
            response = await client.post(
                _MEDIAMTX_WHEP_URL,
                content=sdp_offer,
                headers={"Content-Type": "application/sdp"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"MediaMTX unreachable: {exc}") from exc

    if response.status_code >= 400:
        body_preview = response.text[:200]
        raise HTTPException(
            status_code=502,
            detail=f"MediaMTX rejected WHEP offer (HTTP {response.status_code}): {body_preview}",
        )

    location = response.headers.get("Location")
    if not location:
        raise HTTPException(status_code=502, detail="MediaMTX WHEP response missing Location header")

    # Resolve relative Location headers against the MediaMTX base URL.
    if location.startswith("/"):
        location = f"http://host.docker.internal:8889{location}"
    return response.text, location


async def _delete_mediamtx_session(location: str) -> None:
    """DELETE the resource at the MediaMTX-provided Location (best effort)."""
    try:
        async with httpx.AsyncClient(timeout=_WHEP_TIMEOUT) as client:
            await client.delete(location)
    except httpx.HTTPError as exc:
        logger.warning("WHEP session teardown on MediaMTX failed: %s", exc)


def reset_sessions_for_tests() -> None:
    """Clear the process-local session map — tests only."""
    _sessions.clear()
