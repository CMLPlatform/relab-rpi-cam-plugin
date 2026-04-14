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
import secrets
from typing import TYPE_CHECKING, Protocol, cast

import httpx
import jwt
import websockets
from websockets.exceptions import ConnectionClosed

from app.core.config import settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

# WebSocket message types
_MSG_TYPE_PING = "ping"
_MSG_TYPE_PONG = "pong"
_MSG_TYPE_REQUEST = "request"

# Content-type substrings used to detect binary responses
_BINARY_IMAGE = "image"
_BINARY_OCTET = "octet-stream"


class _AsyncWebSocket(Protocol):
    """Protocol for async WebSocket connections (e.g. websockets library)."""

    async def send(self, data: str | bytes) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


logger = logging.getLogger(__name__)

# Reconnection delay bounds (seconds)
_RECONNECT_MIN = 2.0
_RECONNECT_MAX = 60.0
_MAX_CONCURRENT_COMMANDS = 8
_ASSERTION_AUDIENCE = "relab-rpi-cam-relay"
_ASSERTION_TTL_SECONDS = 120

_WEBSOCKET_CONNECTION_ERRORS: tuple[type[Exception], ...] = (ConnectionClosed, OSError)

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


class _WebSocketConnection:
    def __init__(self, ws: _AsyncWebSocket) -> None:
        self._ws = ws

    async def send(self, data: str) -> None:
        await self._ws.send(data)

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send(data)

    async def recv(self) -> str | bytes:
        return await self._ws.recv()

    async def close(self) -> None:
        await self._ws.close()


@contextlib.asynccontextmanager
async def _websocket_connect(url: str) -> AsyncGenerator[_WebSocketConnection]:
    """Connect to a WebSocket and yield a wrapped connection."""
    raw_ws = cast(
        "_AsyncWebSocket",
        await websockets.connect(
            url,
            max_size=1_048_576,  # 1 MiB limit
            additional_headers={"Authorization": f"Bearer {_build_device_assertion()}"},
        ),
    )
    try:
        yield _WebSocketConnection(raw_ws)
    finally:
        await raw_ws.close()


async def _receive_loop(ws: _WebSocketConnection) -> None:
    """Process command messages from the backend until the connection closes."""
    # Include a local-only key so relayed commands can call the Pi's local API.
    auth_headers = {settings.auth_key_name: settings.local_relay_api_key} if settings.local_relay_api_key else {}
    pending_tasks: set[asyncio.Task[None]] = set()
    command_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_COMMANDS)

    def _on_task_done(task: asyncio.Task[None]) -> None:
        """Callback when a command task completes."""
        pending_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.debug("Command task failed", exc_info=exc)

    async with httpx.AsyncClient(base_url=str(settings.base_url).rstrip("/"), headers=auth_headers) as http:
        try:
            while True:
                raw = await _recv_relay_message(ws)
                if raw is None:
                    # Connection closed or error — let the outer loop reconnect.
                    return
                await _handle_relay_message(ws, http, raw, pending_tasks, _on_task_done, command_semaphore)
        except asyncio.CancelledError:
            await _drain_pending_tasks(pending_tasks, cancel=True)
            raise
        finally:
            await _drain_pending_tasks(pending_tasks)


async def _recv_relay_message(ws: _WebSocketConnection) -> str | bytes | None:
    with contextlib.suppress(*_WEBSOCKET_CONNECTION_ERRORS):
        return await ws.recv()
    return None


async def _handle_relay_message(
    ws: _WebSocketConnection,
    http: httpx.AsyncClient,
    raw: str | bytes,
    pending_tasks: set[asyncio.Task[None]],
    on_task_done: Callable[[asyncio.Task[None]], None],
    command_semaphore: asyncio.Semaphore,
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

    task = asyncio.create_task(_run_command(ws, http, msg, command_semaphore))
    pending_tasks.add(task)
    task.add_done_callback(on_task_done)


async def _run_command(
    ws: _WebSocketConnection,
    http: httpx.AsyncClient,
    msg: dict,
    command_semaphore: asyncio.Semaphore,
) -> None:
    """Run a relayed command with bounded concurrency."""
    async with command_semaphore:
        await _handle_command(ws, http, msg)


async def _drain_pending_tasks(pending_tasks: set[asyncio.Task[None]], *, cancel: bool = False) -> None:
    """Wait for outstanding command tasks, optionally cancelling them first."""
    if not pending_tasks:
        return
    if cancel:
        for task in pending_tasks:
            task.cancel()
    await asyncio.gather(*pending_tasks, return_exceptions=True)


async def _handle_command(ws: _WebSocketConnection, http: httpx.AsyncClient, msg: dict) -> None:
    """Dispatch a single command to the local API and send the response."""
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

    if response.status_code == 403:
        logger.warning("Relay received 403 from local API — check that local_relay_api_key is set correctly")

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


def _build_device_assertion() -> str:
    now = _utc_timestamp()
    payload = {
        "iss": f"camera:{settings.relay_camera_id}",
        "sub": f"camera:{settings.relay_camera_id}",
        "aud": _ASSERTION_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + _ASSERTION_TTL_SECONDS,
        "jti": secrets.token_urlsafe(24),
    }
    return jwt.encode(
        payload,
        settings.relay_private_key_pem,
        algorithm="ES256",
        headers={"kid": settings.relay_key_id},
    )


def _utc_timestamp() -> int:
    from datetime import UTC, datetime  # noqa: PLC0415

    return int(datetime.now(UTC).timestamp())
