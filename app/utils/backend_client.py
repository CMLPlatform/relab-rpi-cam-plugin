"""HTTPS client used by the Pi to push captured images back to the backend.

The Pi authenticates every upload with a fresh short-lived device assertion
(ES256 JWT signed by the relay private key, verified by the backend against
the public key it stored during pairing). The backend accepts the bytes via
``POST /plugins/rpi-cam/cameras/{camera_id}/image-upload`` and returns a
small JSON envelope with the stored image's id and URL.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from pydantic import AnyUrl

from app.core.config import settings
from app.core.runtime_context import get_active_runtime
from app.utils.device_jwt import build_device_assertion
from app.utils.logging import build_log_extra
from relab_rpi_cam_models import DeviceImageUploadAck

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

_UPLOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
_UPLOAD_ENDPOINT_TEMPLATE = "/plugins/rpi-cam/cameras/{camera_id}/image-upload"
_SELF_UNPAIR_ENDPOINT_TEMPLATE = "/plugins/rpi-cam/cameras/{camera_id}/self"
_SELF_UNPAIR_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


class BackendUploadError(RuntimeError):
    """Raised when the backend refuses an upload or the network dies mid-push."""


@dataclass(frozen=True)
class UploadedImageInfo:
    """Result of a successful backend image upload."""

    image_id: str
    image_url: AnyUrl


@dataclass(frozen=True)
class BackendUploadClient:
    """Own the Pi-initiated HTTPS calls into the RELab backend."""

    base_url: str

    async def upload_image(
        self,
        *,
        camera_id: str,
        assertion: str,
        image_bytes: bytes,
        filename: str,
        capture_metadata: Mapping[str, object],
        upload_metadata: Mapping[str, object],
    ) -> UploadedImageInfo:
        """Push a captured JPEG to the backend and validate the ack envelope."""
        url = f"{self.base_url}{_UPLOAD_ENDPOINT_TEMPLATE.format(camera_id=camera_id)}"
        files = {"file": (filename, image_bytes, "image/jpeg")}
        data = {
            "capture_metadata": json.dumps(dict(capture_metadata)),
            "upload_metadata": json.dumps(dict(upload_metadata)),
        }
        headers = {"Authorization": f"Bearer {assertion}"}

        try:
            async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
                response = await client.post(url, files=files, data=data, headers=headers)
        except httpx.HTTPError as exc:
            msg = f"Network error during image upload: {exc}"
            raise BackendUploadError(msg) from exc

        if response.status_code >= 400:
            body_preview = response.text[:200]
            msg = f"Backend rejected upload: HTTP {response.status_code} — {body_preview}"
            raise BackendUploadError(msg)

        try:
            payload = response.json()
        except ValueError as exc:
            msg = f"Backend upload response was not JSON: {response.text[:200]!r}"
            raise BackendUploadError(msg) from exc

        try:
            ack = DeviceImageUploadAck.model_validate(payload)
        except (TypeError, ValueError) as exc:
            msg = f"Backend upload response missing fields: {payload!r}"
            raise BackendUploadError(msg) from exc

        raw_url = ack.image_url
        if not raw_url.startswith("http"):
            raw_url = f"{self.base_url}{raw_url}"
        return UploadedImageInfo(image_id=ack.image_id, image_url=AnyUrl(raw_url))


async def upload_image(
    *,
    image_bytes: bytes,
    filename: str,
    capture_metadata: Mapping[str, object],
    upload_metadata: Mapping[str, object],
) -> UploadedImageInfo:
    """Push a captured JPEG to the backend. Raises BackendUploadError on any failure."""
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

    client = BackendUploadClient(base_url)
    return await client.upload_image(
        camera_id=runtime_state.relay_camera_id,
        assertion=assertion,
        image_bytes=image_bytes,
        filename=filename,
        capture_metadata=capture_metadata,
        upload_metadata=upload_metadata,
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
    endpoint = _SELF_UNPAIR_ENDPOINT_TEMPLATE.format(camera_id=runtime_state.relay_camera_id)
    url = f"{base_url}{endpoint}"

    try:
        assertion = build_device_assertion()
    except (ValueError, TypeError) as exc:
        logger.warning("notify_self_unpair: could not mint device assertion: %s", exc, extra=build_log_extra())
        return

    headers = {"Authorization": f"Bearer {assertion}"}
    try:
        async with httpx.AsyncClient(timeout=_SELF_UNPAIR_TIMEOUT) as client:
            response = await client.delete(url, headers=headers)
        if response.status_code in (204, 200, 404):
            logger.info(
                "notify_self_unpair: backend acknowledged unpair of camera %s",
                runtime_state.relay_camera_id,
                extra=build_log_extra(),
            )
        else:
            logger.warning(
                "notify_self_unpair: backend returned HTTP %d — camera may remain registered",
                response.status_code,
                extra=build_log_extra(),
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "notify_self_unpair: network error reaching backend (%s) — camera may remain registered",
            exc,
            extra=build_log_extra(),
        )
