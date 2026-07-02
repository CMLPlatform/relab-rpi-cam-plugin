"""Runtime-owned WebSocket relay client for the Pi.

When enabled, this module maintains a persistent WebSocket connection to the
backend so the camera can be reached without a public IP address or port
forwarding. The backend sends HTTP-like command messages; this module dispatches
them to the local FastAPI app and sends the response back.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import httpx
from pydantic import ValidationError
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.client import process_exception as classify_websocket_exception
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.core.runtime_state import RuntimeState
from app.core.settings import Settings, settings
from app.observability.logging import build_log_extra, build_security_log_extra
from app.relay.device_jwt import build_device_assertion
from app.relay.state import RelayRuntimeState
from relab_rpi_cam_models import (
    RELAY_COMMAND_FORBIDDEN_DETAIL,
    RELAY_WS_TEXT_FRAME_LIMIT_BYTES,
    RelayCommandEnvelope,
    RelayMessageType,
    RelayResponseEnvelope,
    extract_safe_relay_headers,
    relay_command_is_allowed,
    relay_content_type_is_binary,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from websockets.asyncio.client import ClientConnection

_RELAY_WS_MAX_QUEUE = 8
_RELAY_RECONNECT_INITIAL_DELAY_S = 1.0
_RELAY_RECONNECT_MAX_DELAY_S = 60.0
_LOCAL_DISPATCH_TIMEOUT = httpx.Timeout(connect=2.0, read=30.0, write=30.0, pool=2.0)
_LOCAL_DISPATCH_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)

logger = logging.getLogger(__name__)


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

        url = self.build_url()

        async for ws in _iter_websocket_connections(url, self._runtime_state):
            self._state.mark_connected()
            try:
                logger.info("Relay connected to %s. Waiting for commands.", url, extra=build_log_extra())
                await _receive_loop(
                    ws,
                    relay_state=self._state,
                    runtime_state=self._runtime_state,
                    app_settings=self._settings,
                )
            except ConnectionClosed as exc:
                logger.warning(
                    "Relay connection closed (%s); reconnecting.",
                    _format_relay_connection_error(exc),
                    extra=build_log_extra(),
                )
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Relay loop failed unexpectedly; reconnecting.",
                    extra=build_log_extra(),
                )
                continue
            finally:
                self._state.mark_disconnected()

    def is_configured(self) -> bool:
        """Whether relay credentials are present."""
        return self._runtime_state.relay_enabled

    def build_url(self) -> str:
        """Return the active relay URL including camera identifier."""
        url = httpx.URL(self._runtime_state.relay_backend_url.rstrip("/"))
        return str(url.copy_add_param("camera_id", self._runtime_state.relay_camera_id))


def _format_relay_connection_error(exc: Exception) -> str:
    """Return a concise description for expected transient relay failures."""
    if isinstance(exc, InvalidStatus):
        return f"HTTP {exc.response.status_code}"
    return str(exc) or type(exc).__name__


def _relay_reconnect_delays() -> Iterator[float]:
    """Yield bounded exponential reconnect delays for transient handshake failures."""
    delay = _RELAY_RECONNECT_INITIAL_DELAY_S
    while True:
        yield delay
        delay = min(delay * 2, _RELAY_RECONNECT_MAX_DELAY_S)


async def _iter_websocket_connections(url: str, runtime_state: RuntimeState) -> AsyncIterator[ClientConnection]:
    """Yield relay WebSocket connections, minting a fresh assertion for every handshake attempt."""
    delays = None
    while True:
        try:
            async with websocket_connect(
                url,
                max_size=RELAY_WS_TEXT_FRAME_LIMIT_BYTES,
                max_queue=_RELAY_WS_MAX_QUEUE,
                compression=None,
                additional_headers={"Authorization": f"Bearer {build_device_assertion(runtime_state)}"},
            ) as ws:
                delays = None
                yield ws
        except Exception as exc:
            classified = classify_websocket_exception(exc)
            if classified is exc:
                raise
            if classified is not None:
                raise classified from exc

            if delays is None:
                delays = _relay_reconnect_delays()
            delay = next(delays)
            logger.info(
                "Relay connection attempt failed; reconnecting in %.1fs: %s",
                delay,
                _format_relay_connection_error(exc),
                extra=build_log_extra(),
            )
            await asyncio.sleep(delay)
            continue


async def _receive_loop(
    ws: ClientConnection,
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
    command_semaphore = asyncio.Semaphore(app_settings.relay_max_concurrent_commands)
    send_lock = asyncio.Lock()

    def _on_task_done(task: asyncio.Task[None]) -> None:
        """Callback when a command task completes."""
        pending_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.debug("Command task failed", exc_info=exc, extra=build_log_extra())

    async with httpx.AsyncClient(
        base_url=str(app_settings.base_url).rstrip("/"),
        headers=auth_headers,
        timeout=_LOCAL_DISPATCH_TIMEOUT,
        limits=_LOCAL_DISPATCH_LIMITS,
    ) as http:
        try:
            async for raw in ws:
                await _handle_relay_message(
                    ws,
                    http,
                    raw,
                    pending_tasks,
                    _on_task_done,
                    command_semaphore,
                    send_lock,
                    relay_state=relay_state,
                    max_pending_commands=app_settings.relay_max_pending_commands,
                )
        except asyncio.CancelledError:
            await _drain_pending_tasks(pending_tasks, cancel=True)
            raise
        finally:
            await _drain_pending_tasks(pending_tasks)


async def _handle_relay_message(
    ws: ClientConnection,
    http: httpx.AsyncClient,
    raw: str | bytes,
    pending_tasks: set[asyncio.Task[None]],
    on_task_done: Callable[[asyncio.Task[None]], None],
    command_semaphore: asyncio.Semaphore,
    send_lock: asyncio.Lock | None = None,
    *,
    relay_state: RelayRuntimeState,
    max_pending_commands: int,
) -> None:
    if isinstance(raw, bytes):
        logger.warning("Unexpected binary frame from backend; ignoring.", extra=build_log_extra())
        return

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Received invalid JSON from backend; ignoring.", extra=build_log_extra())
        return

    if not isinstance(msg, dict):
        logger.warning("Received non-object JSON from backend; ignoring.", extra=build_log_extra())
        return

    msg_type = msg.get("type")

    if msg_type == RelayMessageType.PING:
        await ws.send(json.dumps({"type": RelayMessageType.PONG}))
        return

    if msg_type != RelayMessageType.REQUEST:
        return

    # Only real command traffic resets the idle timer — pings and noise don't
    # mean "a user is watching", so we don't hibernate on pings alone.
    relay_state.mark_activity()
    if len(pending_tasks) >= max_pending_commands:
        logger.warning(
            "Security event: relay command rate limit blocked",
            extra=build_security_log_extra(
                event="relay.command.rate_limit",
                outcome="blocked",
                status_code=429,
            ),
        )
        msg_id = msg.get("id")
        if isinstance(msg_id, str) and msg_id:
            await _send_error(ws, msg_id, 429, "Too many pending relay commands.")
        return
    task = asyncio.create_task(_run_command(ws, http, msg, command_semaphore, send_lock))
    pending_tasks.add(task)
    task.add_done_callback(on_task_done)


async def _run_command(
    ws: ClientConnection,
    http: httpx.AsyncClient,
    msg: dict,
    command_semaphore: asyncio.Semaphore,
    send_lock: asyncio.Lock | None = None,
) -> None:
    """Run a relayed command with bounded concurrency."""
    async with command_semaphore:
        await _handle_command(ws, http, msg, send_lock)


async def _drain_pending_tasks(pending_tasks: set[asyncio.Task[None]], *, cancel: bool = False) -> None:
    """Wait for outstanding command tasks, optionally cancelling them first."""
    if not pending_tasks:
        return
    if cancel:
        for task in pending_tasks:
            task.cancel()
    await asyncio.gather(*pending_tasks, return_exceptions=True)


async def _handle_command(
    ws: ClientConnection, http: httpx.AsyncClient, msg: dict, send_lock: asyncio.Lock | None = None
) -> None:
    """Dispatch a single command to the local API and send the response."""
    # A shared per-connection lock is passed in production; a lone direct call
    # gets its own (a single send never contends).
    send_lock = send_lock or asyncio.Lock()
    try:
        envelope = RelayCommandEnvelope.model_validate(msg)
    except ValidationError:
        logger.warning(
            "Security event: malformed relay command rejected",
            extra=build_security_log_extra(
                event="relay.command.malformed",
                outcome="failure",
                status_code=400,
            ),
        )
        msg_id = msg.get("id")
        if isinstance(msg_id, str) and msg_id:
            await _send_error(ws, msg_id, 400, "Malformed relay command.")
        return

    msg_id = envelope.id
    method = envelope.method.upper()
    path = envelope.path
    params = envelope.params
    body = envelope.body
    request_headers = extract_safe_relay_headers(envelope.headers)

    logger.debug("Relay command %s: %s %s", msg_id, method, path)

    if not relay_command_is_allowed(method, path):
        logger.warning(
            "Security event: forbidden relay command rejected",
            extra=build_security_log_extra(
                event="relay.command.forbidden",
                outcome="failure",
                method=method,
                path=path,
                status_code=403,
            ),
        )
        await _send_error(ws, msg_id, 403, RELAY_COMMAND_FORBIDDEN_DETAIL)
        return

    try:
        # Dispatch the raw `path`, but the allowlist above checked its fully
        # unquoted form (repeated-unquote + `..` rejection) — the strictly
        # more-decoded form Starlette also routes on. Keep the check at least as
        # aggressive as request routing if the normalizer is ever loosened.
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

    # `or None`: an empty content-type would fail the envelope's min_length=1.
    content_type = response.headers.get("content-type") or None
    if relay_content_type_is_binary(content_type):
        header = RelayResponseEnvelope(
            id=msg_id,
            status=response.status_code,
            content_type=content_type,
            has_binary=True,
        )
        # The header + binary body must reach the backend as an adjacent pair;
        # concurrent command tasks share this ws, so serialize the two frames.
        async with send_lock:
            await ws.send(header.model_dump_json())
            await ws.send(response.content)
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


async def _send_error(ws: ClientConnection, msg_id: str, status: int, detail: str) -> None:
    response = RelayResponseEnvelope(id=msg_id, status=status, has_binary=False, data={"detail": detail})
    await ws.send(response.model_dump_json())
