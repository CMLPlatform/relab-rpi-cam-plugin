"""WebSocket relay client — connects the RPi camera outbound to the ReLab backend.

When enabled, this module maintains a persistent WebSocket connection to the
backend so the camera can be reached without a public IP address or port
forwarding. The backend sends HTTP-like command messages; this module dispatches
them to the local FastAPI app and sends the response back.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx

from app.core.config import apply_relay_credentials, settings

if TYPE_CHECKING:
    from collections.abc import Callable

websockets: Any = None
ConnectionClosed: Any = None

try:
    import websockets as _websockets
    from websockets.exceptions import ConnectionClosed as _ConnectionClosed
except ImportError:
    pass
else:
    websockets = _websockets
    ConnectionClosed = _ConnectionClosed

# WebSocket message types
_MSG_TYPE_PING = "ping"
_MSG_TYPE_PONG = "pong"
_MSG_TYPE_REQUEST = "request"

# Content-type substrings used to detect binary responses
_BINARY_IMAGE = "image"
_BINARY_OCTET = "octet-stream"


class _StaleKeyError(Exception):
    """Raised when the relay API key has been rotated and needs to be reloaded."""


class _AsyncWebSocket(Protocol):
    """Protocol for async WebSocket connections (e.g. websockets library)."""

    async def send(self, data: str | bytes) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


logger = logging.getLogger(__name__)

# Reconnection delay bounds (seconds)
_RECONNECT_MIN = 2.0
_RECONNECT_MAX = 60.0

if ConnectionClosed is None:
    _WEBSOCKET_CONNECTION_ERRORS: tuple[type[Exception], ...] = (OSError,)
else:
    _WEBSOCKET_CONNECTION_ERRORS = (ConnectionClosed, OSError)

_RELAY_CONNECTION_ERRORS: tuple[type[Exception], ...] = (RuntimeError, *_WEBSOCKET_CONNECTION_ERRORS)


async def run_relay() -> None:
    """Maintain a persistent WebSocket connection to the ReLab backend.

    This is intended to run as a long-lived background task (asyncio.create_task).
    It reconnects automatically with exponential back-off on failure.
    """
    if not _relay_configured():
        logger.info("WebSocket relay not configured; relay will not start.")
        return

    delay = _RECONNECT_MIN
    url = _build_relay_url()

    while True:
        try:
            logger.info("Connecting to ReLab backend relay at %s", url)
            async with _websocket_connect(url) as ws:
                delay = _RECONNECT_MIN  # reset on successful connect
                logger.info("Relay connected. Waiting for commands.")
                await _receive_loop(ws)
        except _RELAY_CONNECTION_ERRORS as exc:
            logger.warning("Relay connection lost. Reconnecting in %.0fs…", delay, exc_info=exc)

        await asyncio.sleep(delay)
        delay = min(delay * 2, _RECONNECT_MAX)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _relay_configured() -> bool:
    return settings.relay_enabled


def _build_relay_url() -> str:
    return f"{settings.relay_backend_url.rstrip('/')}?camera_id={settings.relay_camera_id}"


class _WebSocketContextManager:
    """Minimal async context manager wrapping a websockets connection."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._raw_ws: _AsyncWebSocket | None = None

    async def __aenter__(self) -> _WebSocketConnection:
        # Import websockets lazily so importing this module does not require the
        # optional dependency when relay mode is disabled.
        if websockets is None:
            msg = "The 'websockets' package is required for relay mode. Install it with: uv add websockets"
            raise ImportError(msg)

        raw_ws = cast(
            "_AsyncWebSocket",
            await websockets.connect(
                self._url,
                max_size=1_048_576,  # 1 MiB limit
                additional_headers={"Authorization": f"Bearer {settings.relay_api_key}"},
            ),
        )
        self._raw_ws = raw_ws
        return _WebSocketConnection(raw_ws)

    async def __aexit__(self, *_: object) -> None:
        if self._raw_ws:
            await self._raw_ws.close()


class _WebSocketConnection:
    def __init__(self, ws: _AsyncWebSocket) -> None:
        self._ws = ws

    async def send(self, data: str) -> None:
        await self._ws.send(data)

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send(data)

    async def recv(self) -> str | bytes:
        return await self._ws.recv()


def _websocket_connect(url: str) -> _WebSocketContextManager:
    return _WebSocketContextManager(url)


async def _receive_loop(ws: _WebSocketConnection) -> None:
    """Process command messages from the backend until the connection closes."""
    # Include the relay API key so the local API accepts relayed commands.
    auth_headers = {settings.auth_key_name: settings.relay_api_key} if settings.relay_api_key else {}
    pending_tasks: set[asyncio.Task[None]] = set()

    def _on_task_done(task: asyncio.Task[None]) -> None:
        """Callback when a command task completes. Checks for stale key errors."""
        pending_tasks.discard(task)
        if task.cancelled():
            return

        exc = task.exception()
        if exc is None:
            return

        if isinstance(exc, _StaleKeyError):
            # Signal reconnection by closing the WebSocket, which will exit _receive_loop
            _task = asyncio.create_task(_close_ws_for_reconnect(ws))
            # Keep a reference so the task isn't garbage-collected immediately
            pending_tasks.add(_task)
            _task.add_done_callback(pending_tasks.discard)
            return

        logger.debug("Command task failed", exc_info=exc)

    async with httpx.AsyncClient(base_url=str(settings.base_url).rstrip("/"), headers=auth_headers) as http:
        while True:
            raw = await _recv_relay_message(ws)
            if raw is None:
                # Connection closed or error — let the outer loop reconnect.
                return
            await _handle_relay_message(ws, http, raw, pending_tasks, _on_task_done)


async def _recv_relay_message(ws: _WebSocketConnection) -> str | bytes | None:
    with contextlib.suppress(Exception):
        return await ws.recv()
    return None


async def _handle_relay_message(
    ws: _WebSocketConnection,
    http: httpx.AsyncClient,
    raw: str | bytes,
    pending_tasks: set[asyncio.Task[None]],
    on_task_done: Callable[[asyncio.Task[None]], None],
) -> None:
    if isinstance(raw, bytes):
        logger.warning("Unexpected binary frame from backend; ignoring.")
        return

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Received invalid JSON from backend; ignoring.")
        return

    msg_type = msg.get("type")

    if msg_type == _MSG_TYPE_PING:
        await ws.send(json.dumps({"type": _MSG_TYPE_PONG}))
        return

    if msg_type != _MSG_TYPE_REQUEST:
        return

    task = asyncio.create_task(_handle_command(ws, http, msg))
    pending_tasks.add(task)
    task.add_done_callback(on_task_done)


async def _handle_command(ws: _WebSocketConnection, http: httpx.AsyncClient, msg: dict) -> None:
    """Dispatch a single command to the local API and send the response.

    Raises _StaleKeyError if the relay API key has been rotated (403 response).
    """
    msg_id = msg.get("id", "")
    method: str = msg.get("method", "GET").upper()
    path: str = msg.get("path", "/")
    params: dict = msg.get("params") or {}
    body: dict | None = msg.get("body")

    logger.debug("Relay command %s: %s %s", msg_id, method, path)

    try:
        response = await http.request(method, path, params=params, json=body, timeout=30.0)
    except httpx.HTTPError as exc:
        await _send_error(ws, msg_id, 503, str(exc))
        return

    # Detect if the relay API key has been rotated (backend returns 403)
    if response.status_code == 403:
        logger.warning("Relay received 403 Unauthorized — API key may have been rotated")
        message = "Relay API key rotated"
        raise _StaleKeyError(message)

    content_type = response.headers.get("content-type", "")
    is_binary = _BINARY_IMAGE in content_type or _BINARY_OCTET in content_type

    if is_binary:
        # Send JSON header first, then binary frame
        header = json.dumps(
            {
                "id": msg_id,
                "type": "response",
                "status": response.status_code,
                "content_type": content_type,
                "has_binary": True,
            },
        )
        await ws.send(header)
        await ws.send_bytes(response.content)
    else:
        try:
            data = response.json()
        except ValueError:
            data = response.text

        await ws.send(
            json.dumps(
                {
                    "id": msg_id,
                    "type": "response",
                    "status": response.status_code,
                    "content_type": content_type,
                    "has_binary": False,
                    "data": data,
                },
            ),
        )


async def _send_error(ws: _WebSocketConnection, msg_id: str, status: int, detail: str) -> None:
    await ws.send(
        json.dumps(
            {
                "id": msg_id,
                "type": "response",
                "status": status,
                "has_binary": False,
                "data": {"detail": detail},
            },
        ),
    )


async def _close_ws_for_reconnect(ws: _WebSocketConnection) -> None:
    """Close the WebSocket connection to trigger reconnection with new credentials.

    Called when the relay API key is detected to have been rotated (403 response).
    Reloads credentials before closing so the next connection uses the new key.
    """
    logger.info("Closing relay connection to reconnect with new API key.")
    apply_relay_credentials()
    with contextlib.suppress(Exception):
        await ws._ws.close()  # noqa: SLF001
