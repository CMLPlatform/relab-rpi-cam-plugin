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
from app.utils.device_jwt import build_device_assertion

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

_UPLOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
_UPLOAD_ENDPOINT_TEMPLATE = "/plugins/rpi-cam/cameras/{camera_id}/image-upload"


class BackendUploadError(RuntimeError):
    """Raised when the backend refuses an upload or the network dies mid-push."""


@dataclass(frozen=True)
class UploadedImageInfo:
    """Result of a successful backend image upload."""

    image_id: str
    image_url: AnyUrl


async def upload_image(
    *,
    image_bytes: bytes,
    filename: str,
    capture_metadata: Mapping[str, object],
    upload_metadata: Mapping[str, object],
) -> UploadedImageInfo:
    """Push a captured JPEG to the backend. Raises BackendUploadError on any failure."""
    if not settings.pairing_backend_url:
        msg = "Backend upload requested but PAIRING_BACKEND_URL is not configured."
        raise BackendUploadError(msg)
    if not settings.relay_enabled:
        msg = "Backend upload requested but relay credentials are missing — device is unpaired."
        raise BackendUploadError(msg)

    base_url = settings.pairing_backend_url.rstrip("/")
    endpoint = _UPLOAD_ENDPOINT_TEMPLATE.format(camera_id=settings.relay_camera_id)
    url = f"{base_url}{endpoint}"

    try:
        assertion = build_device_assertion()
    except (ValueError, TypeError) as exc:
        msg = f"Failed to mint device assertion: {exc}"
        raise BackendUploadError(msg) from exc

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
        msg_0 = f"Network error during image upload: {exc}"
        raise BackendUploadError(msg_0) from exc

    if response.status_code >= 400:
        body_preview = response.text[:200]
        msg_0 = f"Backend rejected upload: HTTP {response.status_code} — {body_preview}"
        raise BackendUploadError(
            msg_0,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        msg_1 = f"Backend upload response was not JSON: {response.text[:200]!r}"
        raise BackendUploadError(msg_1) from exc

    try:
        image_id = str(payload["image_id"])
        image_url = AnyUrl(str(payload["image_url"]))
    except (KeyError, TypeError, ValueError) as exc:
        msg_2 = f"Backend upload response missing fields: {payload!r}"
        raise BackendUploadError(msg_2) from exc

    return UploadedImageInfo(image_id=image_id, image_url=image_url)
