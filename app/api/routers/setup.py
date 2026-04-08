"""Setup and QR-code endpoint for RPi camera onboarding."""

from __future__ import annotations

import io

import qrcode
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, StreamingResponse

from app.core.config import settings
from app.utils.pairing import STATUS_PAIRED, get_pairing_state

_STATUS_ERROR = "error"

router = APIRouter(tags=["setup"])

_CSS = """
body{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 20px;line-height:1.6}
h1{font-size:1.8rem;margin-bottom:4px}h2{font-size:1.2rem;margin-top:2rem}
table{width:100%;border-collapse:collapse;margin:12px 0}
td{padding:8px 12px;border:1px solid #ddd;word-break:break-all}
td:first-child{font-weight:600;width:160px;word-break:normal}
code{background:#f0f0f0;padding:2px 6px;border-radius:4px;font-size:.9em}
.online{color:#2e7d32;font-weight:700}.offline{color:#c62828;font-weight:700}
img{display:block;margin:1rem 0}
"""


@router.get("/setup", response_class=HTMLResponse)
async def setup_page() -> str:
    """HTML page showing camera config status, pairing code, and QR code."""
    base_url = str(settings.base_url).rstrip("/")
    pairing = get_pairing_state()

    # ── Pairing section ──────────────────────────────────────────────────
    pairing_html = ""
    # Auto-refresh while waiting for pairing (stops after paired/connected)
    auto_refresh = ""
    if pairing.status in ("waiting", "registering") and pairing.code:
        auto_refresh = '<meta http-equiv="refresh" content="5">'
        pairing_html = f"""
  <h2>Pairing</h2>
  <p>Enter this code in the <strong>ReLab app</strong> (Cameras &rarr; Add Camera)
     to pair this camera automatically.</p>
  <div style="font-size:3rem;font-weight:800;letter-spacing:.3em;font-family:monospace;
              background:#f5f5f5;display:inline-block;padding:12px 24px;border-radius:12px;
              border:2px dashed #999;margin:8px 0">{pairing.code}</div>
  <p style="opacity:.6">Or scan the QR code on a mobile device:</p>
  <img src="/qr-setup" alt="QR pairing code" width="200" height="200">
  <p style="color:#888;font-size:.85em">Code expires in 10 minutes. A new one will be generated automatically.</p>
"""
    elif pairing.status == STATUS_PAIRED:
        auto_refresh = '<meta http-equiv="refresh" content="3">'
        pairing_html = """
  <h2>Pairing</h2>
  <p class="online">Paired successfully! Connecting to backend&hellip;</p>
"""
    elif pairing.status == _STATUS_ERROR:
        auto_refresh = '<meta http-equiv="refresh" content="5">'
        pairing_html = f"""
  <h2>Pairing</h2>
  <p class="offline">{pairing.error or "Pairing error — retrying&hellip;"}</p>
"""

    # ── Connection info ──────────────────────────────────────────────────
    connection_html = ""
    if settings.relay_enabled:
        connection_html = f"""
  <h2>Connection</h2>
  <p>WebSocket relay: <span class="online">connected</span></p>
  <table>
    <tr><td>Backend URL</td><td><code>{settings.relay_backend_url or "not set"}</code></td></tr>
    <tr><td>Camera ID</td><td>{"<em>set</em>" if settings.relay_camera_id else "<em>not set</em>"}</td></tr>
  </table>
"""
    elif pairing.status not in ("waiting", "registering", "paired"):
        connection_html = """
  <h2>Connection</h2>
  <p>WebSocket relay: <span class="offline">not configured</span></p>
"""

    # Always show the HTTP URL as a fallback reference
    connection_html += f"""
  <h2>Camera URL (HTTP mode)</h2>
  <p><code>{base_url}</code></p>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  {auto_refresh}
  <title>RPi Camera — Setup</title>
  <style>{_CSS}</style>
</head>
<body>
  <h1>RPi Camera Setup</h1>
  {pairing_html}
  {connection_html}
</body>
</html>"""


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
