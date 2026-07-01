"""HTTPS client used by the Pi to push captured images back to the backend.

The Pi authenticates every upload with a fresh short-lived device assertion
(ES256 JWT signed by the relay private key, verified by the backend against
the public key it stored during pairing). The backend accepts the bytes via
``POST /v1/plugins/rpi-cam/device/cameras/{camera_id}/image-upload`` and returns a
small JSON envelope with the stored image's id and URL.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
from pydantic import AnyUrl

from app.core.runtime_context import get_active_runtime
from app.core.settings import settings, validate_endpoint_transport
from app.observability.logging import build_log_extra
from app.relay.device_jwt import build_device_assertion
from relab_rpi_cam_models import DeviceImageUploadAck, DevicePreviewThumbnailAck

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

_UPLOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
_UPLOAD_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)
_UPLOAD_ENDPOINT_TEMPLATE = "/v1/plugins/rpi-cam/device/cameras/{camera_id}/image-upload"
_PREVIEW_THUMBNAIL_ENDPOINT_TEMPLATE = "/v1/plugins/rpi-cam/device/cameras/{camera_id}/preview-thumbnail-upload"
_SELF_UNPAIR_ENDPOINT_TEMPLATE = "/v1/plugins/rpi-cam/device/cameras/{camera_id}/self"
_SELF_UNPAIR_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


@cache
def _get_client() -> httpx.AsyncClient:
    """Return the process-wide backend HTTP client, created on first use.

    Reused across uploads so the keepalive pool in ``_UPLOAD_LIMITS`` survives
    between requests instead of being rebuilt and torn down on every call.
    """
    return httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT, limits=_UPLOAD_LIMITS, follow_redirects=False)


async def aclose_client() -> None:
    """Close the shared backend client. Called once at application shutdown."""
    if _get_client.cache_info().currsize:
        await _get_client().aclose()
        _get_client.cache_clear()


def _camera_endpoint(template: str, camera_id: str) -> str:
    return template.format(camera_id=quote(camera_id, safe=""))


def _resolve_backend_media_url(raw_url: str, *, base_url: str, field_name: str) -> AnyUrl:
    if raw_url.startswith("/") and not raw_url.startswith("//"):
        raw_url = f"{base_url}{raw_url}"
    try:
        validate_endpoint_transport(raw_url, setting_name=field_name, app_env=settings.app_env)
    except ValueError as exc:
        raise BackendUploadError(str(exc)) from exc
    return AnyUrl(raw_url)


class BackendUploadError(RuntimeError):
    """Raised when the backend refuses an upload or the network dies mid-push."""


@dataclass(frozen=True)
class UploadedImageInfo:
    """Result of a successful backend image upload."""

    image_id: str
    image_url: AnyUrl


@dataclass(frozen=True)
class UploadedPreviewThumbnailInfo:
    """Result of a successful cached preview-thumbnail upload."""

    preview_thumbnail_url: AnyUrl


async def _post_file(
    *,
    url: str,
    files: dict,
    headers: dict,
    data: dict | None = None,
    upload_label: str,
) -> dict:
    try:
        response = await _get_client().post(url, files=files, data=data, headers=headers)
    except httpx.HTTPError as exc:
        msg = f"Network error during {upload_label}: {exc}"
        raise BackendUploadError(msg) from exc

    if response.status_code >= 400:
        body_preview = response.text[:200]
        msg = f"Backend rejected {upload_label}: HTTP {response.status_code} — {body_preview}"
        raise BackendUploadError(msg)

    try:
        return response.json()
    except ValueError as exc:
        msg = f"Backend {upload_label} response was not JSON: {response.text[:200]!r}"
        raise BackendUploadError(msg) from exc


async def _get_upload_context() -> tuple[str, str, str]:
    """Return (base_url, camera_id, assertion) ready for upload, or raise BackendUploadError."""
    runtime_state = get_active_runtime().runtime_state
    if not settings.pairing_backend_url:
        msg = "Backend upload requested but PAIRING_BACKEND_URL is not configured."
        raise BackendUploadError(msg)
    if not runtime_state.relay_enabled:
        msg = "Backend upload requested but relay credentials are missing — device is unpaired."
        raise BackendUploadError(msg)
    base_url = settings.pairing_backend_url.rstrip("/")
    try:
        assertion = build_device_assertion()
    except (ValueError, TypeError) as exc:
        msg = f"Failed to mint device assertion: {exc}"
        raise BackendUploadError(msg) from exc
    return base_url, runtime_state.relay_camera_id, assertion


async def upload_image(
    *,
    image_bytes: bytes,
    filename: str,
    capture_metadata: Mapping[str, object],
    upload_metadata: Mapping[str, object],
) -> UploadedImageInfo:
    """Push a captured JPEG to the backend. Raises BackendUploadError on any failure."""
    base_url, camera_id, assertion = await _get_upload_context()
    url = f"{base_url}{_camera_endpoint(_UPLOAD_ENDPOINT_TEMPLATE, camera_id)}"
    payload = await _post_file(
        url=url,
        files={"file": (filename, image_bytes, "image/jpeg")},
        data={
            "capture_metadata": json.dumps(dict(capture_metadata)),
            "upload_metadata": json.dumps(dict(upload_metadata)),
        },
        headers={"Authorization": f"Bearer {assertion}"},
        upload_label="image upload",
    )
    try:
        ack = DeviceImageUploadAck.model_validate(payload)
    except (TypeError, ValueError) as exc:
        msg = f"Backend upload response missing fields: {payload!r}"
        raise BackendUploadError(msg) from exc
    return UploadedImageInfo(
        image_id=ack.image_id,
        image_url=_resolve_backend_media_url(ack.image_url, base_url=base_url, field_name="image_url"),
    )


async def upload_preview_thumbnail(
    *,
    image_bytes: bytes,
    filename: str = "preview-thumbnail.jpg",
) -> UploadedPreviewThumbnailInfo:
    """Push a cached preview thumbnail to the backend. Raises BackendUploadError on failure."""
    base_url, camera_id, assertion = await _get_upload_context()
    url = f"{base_url}{_camera_endpoint(_PREVIEW_THUMBNAIL_ENDPOINT_TEMPLATE, camera_id)}"
    payload = await _post_file(
        url=url,
        files={"file": (filename, image_bytes, "image/jpeg")},
        headers={"Authorization": f"Bearer {assertion}"},
        upload_label="preview thumbnail upload",
    )
    try:
        ack = DevicePreviewThumbnailAck.model_validate(payload)
    except (TypeError, ValueError) as exc:
        msg = f"Backend preview thumbnail response missing fields: {payload!r}"
        raise BackendUploadError(msg) from exc
    return UploadedPreviewThumbnailInfo(
        preview_thumbnail_url=_resolve_backend_media_url(
            ack.preview_thumbnail_url,
            base_url=base_url,
            field_name="preview_thumbnail_url",
        )
    )


async def notify_self_unpair() -> None:
    """Tell the backend to delete this camera's registration.

    Called when the operator unpairs via the local /setup page. This is
    best-effort — if the backend is unreachable the camera will remain in the
    backend's database until the operator deletes it manually from the app.
    Any error is logged as a warning, never raised, so the local unpair always
    completes regardless of backend connectivity.
    """
    runtime_state = get_active_runtime().runtime_state
    if not settings.pairing_backend_url:
        logger.debug("notify_self_unpair: no PAIRING_BACKEND_URL, skipping")
        return
    if not runtime_state.relay_enabled:
        logger.debug("notify_self_unpair: relay credentials missing, skipping")
        return

    base_url = settings.pairing_backend_url.rstrip("/")
    endpoint = _camera_endpoint(_SELF_UNPAIR_ENDPOINT_TEMPLATE, runtime_state.relay_camera_id)
    url = f"{base_url}{endpoint}"

    try:
        assertion = build_device_assertion()
    except (ValueError, TypeError) as exc:
        logger.warning("notify_self_unpair: could not mint device assertion: %s", exc, extra=build_log_extra())
        return

    headers = {"Authorization": f"Bearer {assertion}"}
    try:
        response = await _get_client().delete(url, headers=headers, timeout=_SELF_UNPAIR_TIMEOUT)
        if response.status_code in (204, 200, 404):
            logger.info(
                "notify_self_unpair: backend acknowledged unpair of camera %s",
                runtime_state.relay_camera_id,
                extra=build_log_extra(),
            )
        else:
            logger.warning(
                "notify_self_unpair: backend returned HTTP %d — camera may remain registered "
                "(url=%s server=%s body=%r)",
                response.status_code,
                response.request.url,
                response.headers.get("server", "?"),
                response.text[:500],
                extra=build_log_extra(),
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "notify_self_unpair: network error reaching backend (%s) — camera may remain registered",
            exc,
            extra=build_log_extra(),
        )
