"""Setup endpoint for RPi camera onboarding."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core.templates_config import templates
from app.utils.pairing import PAIRING_CODE_TTL_SECONDS, STATUS_PAIRED, get_pairing_state

_STATUS_ERROR = "error"

router = APIRouter(tags=["setup"])


@router.get("/setup")
async def setup_page(request: Request) -> HTMLResponse:
    """HTML page showing camera config status and pairing code."""
    base_url = str(settings.base_url).rstrip("/")
    pairing = get_pairing_state()
    pairing_expires_at_iso = pairing.expires_at.isoformat() if pairing.expires_at else ""

    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "pairing": pairing,
            "pairing_expires_at_iso": pairing_expires_at_iso,
            "relay_enabled": settings.relay_enabled,
            "relay_backend_url": settings.relay_backend_url,
            "relay_camera_id": settings.relay_camera_id,
            "base_url": base_url,
            "status_paired": STATUS_PAIRED,
            "status_error": _STATUS_ERROR,
            "pairing_code_ttl_seconds": PAIRING_CODE_TTL_SECONDS,
        },
    )
