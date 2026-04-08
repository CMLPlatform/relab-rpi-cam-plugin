"""Setup and QR-code endpoint for RPi camera onboarding."""

from __future__ import annotations

import io

import qrcode
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from app.core.config import settings
from app.core.templates_config import templates
from app.utils.pairing import STATUS_PAIRED, get_pairing_state

_STATUS_ERROR = "error"

router = APIRouter(tags=["setup"])


@router.get("/setup")
async def setup_page(request: Request) -> HTMLResponse:
    """HTML page showing camera config status, pairing code, and QR code."""
    base_url = str(settings.base_url).rstrip("/")
    pairing = get_pairing_state()

    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "pairing": pairing,
            "relay_enabled": settings.relay_enabled,
            "relay_backend_url": settings.relay_backend_url,
            "relay_camera_id": settings.relay_camera_id,
            "base_url": base_url,
            "status_paired": STATUS_PAIRED,
            "status_error": _STATUS_ERROR,
        },
    )


@router.get("/qr-setup", response_class=StreamingResponse, include_in_schema=False)
async def setup_qr() -> StreamingResponse:
    """Return a QR-code PNG. Encodes pairing code when in pairing mode, otherwise the base URL."""
    pairing = get_pairing_state()
    if pairing.status in ("waiting", "registering") and pairing.code:
        qr_data = f"relab-pair:{pairing.code}"
    else:
        qr_data = str(settings.base_url).rstrip("/")
    img = qrcode.make(qr_data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")
