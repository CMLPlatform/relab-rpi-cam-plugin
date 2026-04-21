"""Runtime-owned WebSocket relay client for the Pi.

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
from typing import TYPE_CHECKING, Protocol, cast

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.api.services.device_jwt import build_device_assertion
from app.api.services.relay_state import RelayRuntimeState
from app.core.config import Settings, settings
from app.core.runtime_state import RuntimeState
from app.observability.logging import build_log_extra
from relab_rpi_cam_models import (
    SAFE_RELAY_TRACE_HEADERS,
    RelayCommandEnvelope,
    RelayMessageType,
    RelayResponseEnvelope,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

# WebSocket message types
_MSG_TYPE_PING = RelayMessageType.PING
_MSG_TYPE_PONG = RelayMessageType.PONG
_MSG_TYPE_REQUEST = RelayMessageType.REQUEST

# Content-type substrings used to detect binary responses. ``video`` covers
# LL-HLS segments (``video/mp4`` / ``video/iso.segment``) that the HLS proxy
# router serves from MediaMTX through the relay.
_BINARY_IMAGE = "image"
_BINARY_OCTET = "octet-stream"
_BINARY_VIDEO = "video"


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

_WEBSOCKET_CONNECTION_ERRORS: tuple[type[Exception], ...] = (ConnectionClosed, InvalidStatus, OSError)

_RELAY_CONNECTION_ERRORS: tuple[type[Exception], ...] = (RuntimeError, *_WEBSOCKET_CONNECTION_ERRORS)


class RelayService:
    """Runtime-owned outbound WebSocket relay service."""

    def __init__(
        self,
        *,
        state: RelayRuntimeState,
        runtime_state: RuntimeState,
        app_settings: Settings = settings,
    ) -> None:
        self._state = state
        self._runtime_state = runtime_state
        self._settings = app_settings

    async def run_forever(self) -> None:
        """Maintain a persistent WebSocket connection to the ReLab backend."""
        if not self.is_configured():
            logger.info("WebSocket relay not configured; relay will not start.")
            return

        delay = _RECONNECT_MIN
        url = self.build_url()

        while True:
            try:
                logger.info("Connecting to ReLab backend relay at %s", url, extra=build_log_extra())
                async with _websocket_connect(url) as ws:
                    delay = _RECONNECT_MIN
                    self._state.mark_connected()
                    logger.info("Relay connected. Waiting for commands.", extra=build_log_extra())
                    await _receive_loop(
                        ws,
                        relay_state=self._state,
                        runtime_state=self._runtime_state,
                        app_settings=self._settings,
                    )
            except _RELAY_CONNECTION_ERRORS as exc:
                logger.warning(
                    "Relay connection lost (%s). Reconnecting in %.0fs…",
                    _format_relay_connection_error(exc),
                    delay,
                    extra=build_log_extra(),
                )
            except Exception:
                logger.exception(
                    "Relay loop failed unexpectedly. Reconnecting in %.0fs…",
                    delay,
                    extra=build_log_extra(),
                )
            finally:
                self._state.mark_disconnected()

            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)

    def is_configured(self) -> bool:
        """Whether relay credentials are present."""
        return self._runtime_state.relay_enabled

    def build_url(self) -> str:
        """Return the active relay URL including camera identifier."""
        return f"{self._runtime_state.relay_backend_url.rstrip('/')}?camera_id={self._runtime_state.relay_camera_id}"


def _format_relay_connection_error(exc: Exception) -> str:
    """Return a concise description for expected transient relay failures."""
    if isinstance(exc, InvalidStatus):
        response = exc.response
        return f"HTTP {response.status_code}"
    return str(exc) or type(exc).__name__


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
            additional_headers={"Authorization": f"Bearer {build_device_assertion()}"},
        ),
    )
    try:
        yield _WebSocketConnection(raw_ws)
    finally:
        await raw_ws.close()


async def _receive_loop(
    ws: _WebSocketConnection,
    *,
    relay_state: RelayRuntimeState,
    runtime_state: RuntimeState,
    app_settings: Settings = settings,
) -> None:
    """Process command messages from the backend until the connection closes."""
    # Include a local-only key so relayed commands can call the Pi's local API.
    auth_headers = (
        {app_settings.auth_key_name: runtime_state.local_relay_api_key} if runtime_state.local_relay_api_key else {}
    )
    pending_tasks: set[asyncio.Task[None]] = set()
    command_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_COMMANDS)

    def _on_task_done(task: asyncio.Task[None]) -> None:
        """Callback when a command task completes."""
        pending_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.debug("Command task failed", exc_info=exc, extra=build_log_extra())

    async with httpx.AsyncClient(base_url=str(app_settings.base_url).rstrip("/"), headers=auth_headers) as http:
        try:
            while True:
                raw = await _recv_relay_message(ws)
                if raw is None:
                    # Connection closed or error — let the outer loop reconnect.
                    return
                await _handle_relay_message(
                    ws,
                    http,
                    raw,
                    pending_tasks,
                    _on_task_done,
                    command_semaphore,
                    relay_state=relay_state,
                )
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
    *,
    relay_state: RelayRuntimeState,
) -> None:
    if isinstance(raw, bytes):
        logger.warning("Unexpected binary frame from backend; ignoring.", extra=build_log_extra())
        return

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Received invalid JSON from backend; ignoring.", extra=build_log_extra())
        return

    msg_type = msg.get("type")

    if msg_type == _MSG_TYPE_PING:
        await ws.send(json.dumps({"type": RelayMessageType.PONG}))
        return

    if msg_type != _MSG_TYPE_REQUEST:
        return

    # Only real command traffic resets the idle timer — pings and noise don't
    # mean "a user is watching", so we don't hibernate on pings alone.
    relay_state.mark_activity()
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
    envelope = RelayCommandEnvelope.model_validate(msg)
    msg_id = envelope.id
    method = envelope.method.upper()
    path = envelope.path
    params = envelope.params
    body = envelope.body
    request_headers = _extract_trace_headers(envelope.headers)

    logger.debug("Relay command %s: %s %s", msg_id, method, path)

    try:
        response = await http.request(
            method,
            path,
            params=params,
            json=body,
            headers=request_headers or None,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        await _send_error(ws, msg_id, 503, str(exc))
        return

    if response.status_code == 403:
        logger.warning("Relay received 403 from local API — check that local_relay_api_key is set correctly")

    content_type = response.headers.get("content-type", "")
    is_binary = _BINARY_IMAGE in content_type or _BINARY_OCTET in content_type or _BINARY_VIDEO in content_type

    if is_binary:
        # Send JSON header first, then binary frame
        header = RelayResponseEnvelope(
            id=msg_id,
            status=response.status_code,
            content_type=content_type,
            has_binary=True,
        )
        await ws.send(header.model_dump_json())
        await ws.send_bytes(response.content)
    else:
        try:
            data = response.json()
        except ValueError:
            data = response.text

        await ws.send(
            RelayResponseEnvelope(
                id=msg_id,
                status=response.status_code,
                content_type=content_type,
                has_binary=False,
                data=data,
            ).model_dump_json()
        )


async def _send_error(ws: _WebSocketConnection, msg_id: str, status: int, detail: str) -> None:
    response = RelayResponseEnvelope(id=msg_id, status=status, has_binary=False, data={"detail": detail})
    await ws.send(response.model_dump_json())


def _extract_trace_headers(headers: object) -> dict[str, str]:
    """Return relay-safe tracing headers from an incoming command payload."""
    if not isinstance(headers, dict):
        return {}

    trace_headers: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        normalized_name = name.lower()
        if normalized_name in SAFE_RELAY_TRACE_HEADERS:
            trace_headers[normalized_name] = value
    return trace_headers
