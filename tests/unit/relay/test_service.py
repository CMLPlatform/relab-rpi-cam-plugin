"""Tests for relay module helpers."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedOK, InvalidStatus
from websockets.http11 import Response

from app.core.runtime_state import RuntimeState
from app.relay import service as relay_mod
from app.relay.service import (
    RelayService,
    _format_relay_connection_error,
    _handle_command,
    _handle_relay_message,
    _iter_websocket_connections,
    _receive_loop,
    _send_error,
)
from app.relay.state import RelayRuntimeState
from relab_rpi_cam_models import RELAY_COMMAND_FORBIDDEN_DETAIL, RELAY_WS_TEXT_FRAME_LIMIT_BYTES
from tests.constants import EXAMPLE_RELAY_BACKEND_URL, EXAMPLE_RELAY_BACKEND_URL_WITH_CAMERA_ID

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection  # used in cast("ClientConnection", ...) below

RELAY_AUTH_SCHEME = "device_assertion"
RELAY_KEY_ID = "key-1"
RELAY_PRIVATE_KEY_PEM = "private-key"
BEARER_JWT = "Bearer jwt"
MESSAGE_ID = "msg-1"
MALFORMED_MESSAGE_ID = "msg-bad"
OVERFLOW_MESSAGE_ID = "overflow"
DETAIL = "oops"
PONG_TYPE = "pong"
RELAY_403_FRAGMENT = "Relay received 403"
RELAY_COMMAND_ERROR = "boom"
HLS_SEGMENT_BYTES = b"\x00\x00\x00\x18ftypmp42"
HLS_SEGMENT_CONTENT_TYPE = "video/mp4"
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00"
TRACESTATE = "vendor=value"
BAGGAGE = "user_id=42"
HTTP_502_SUMMARY = "HTTP 502"
NETWORK_DOWN = "network down"
RELAY_RECONNECT_LOG = "Relay connection closed (no close frame received or sent); reconnecting."
LOCAL_ACCESS_PATH = "/system/local-access"
CAMERA_PATH = "/camera"
PREVIEW_HLS_SEGMENT_PATH = "/preview/hls/cam-preview/seg.mp4"
BLOCKED_RELAY_PATH = "/auth/login"
BLOCKED_RELATIVE_RELAY_PATH = "auth/login"
BLOCKED_RELAY_METHOD = "POST"
BLOCKED_RELAY_DETAIL = RELAY_COMMAND_FORBIDDEN_DETAIL
SECURITY_RELAY_FORBIDDEN_EVENT = "relay.command.forbidden"
SECURITY_RELAY_MALFORMED_EVENT = "relay.command.malformed"
SECURITY_RELAY_RATE_LIMIT_EVENT = "relay.command.rate_limit"


def _log_record(caplog: pytest.LogCaptureFixture, event: str) -> Any:
    """Return a structured log record with test-owned dynamic attributes."""
    return cast("Any", next(record for record in caplog.records if getattr(record, "event", "") == event))


async def _noop_asgi_app(_scope: Any, _receive: Any, _send: Any) -> None:
    """ASGI stand-in for receive-loop tests that never dispatch a request."""


async def _json_ok_asgi_app(scope: Any, _receive: Any, send: Any) -> None:
    """Minimal ASGI app returning {"ok": true} for any HTTP request."""
    assert scope["type"] == "http"
    await send(
        {"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]}
    )
    await send({"type": "http.response.body", "body": b'{"ok": true}'})


class TestRelayServiceConfig:
    """Tests for runtime-backed relay configuration detection."""

    def test_returns_false_when_no_credentials(self) -> None:
        """Should return False if any required credential is missing."""
        service = RelayService(state=RelayRuntimeState(), runtime_state=RuntimeState())
        assert service.is_configured() is False

    def test_returns_true_when_all_set(self) -> None:
        """Should return True if all required credentials are set."""
        runtime_state = RuntimeState()
        runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)
        assert service.is_configured() is True

    def test_returns_false_when_partial(self) -> None:
        """Should return False if only some credentials are set."""
        runtime_state = RuntimeState(relay_backend_url=EXAMPLE_RELAY_BACKEND_URL, relay_camera_id="cam-1")
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)
        assert service.is_configured() is False


class TestRelayServiceUrl:
    """Tests for relay URL construction."""

    def test_builds_url_with_camera_id(self) -> None:
        """Should build the relay URL with the camera_id query parameter."""
        runtime_state = RuntimeState(relay_backend_url=f"{EXAMPLE_RELAY_BACKEND_URL}/", relay_camera_id="cam-42")
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)
        assert service.build_url() == EXAMPLE_RELAY_BACKEND_URL_WITH_CAMERA_ID

    def test_encodes_camera_id_query_value(self) -> None:
        """Camera IDs must not be able to alter the relay query string."""
        runtime_state = RuntimeState(
            relay_backend_url=f"{EXAMPLE_RELAY_BACKEND_URL}/",
            relay_camera_id="cam-42&role=admin",
        )
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)

        assert service.build_url() == f"{EXAMPLE_RELAY_BACKEND_URL}?camera_id=cam-42%26role%3Dadmin"


class TestRelayConnectionErrorFormatting:
    """Tests for concise formatting of expected reconnect errors."""

    def test_invalid_status_formats_as_http_code(self) -> None:
        """Handshake failures should log as short HTTP status summaries."""
        response = Response(502, "Bad Gateway", Headers(), b"error code: 502")
        exc = InvalidStatus(response)

        assert _format_relay_connection_error(exc) == HTTP_502_SUMMARY

    def test_generic_error_uses_message(self) -> None:
        """Non-handshake reconnect errors should use their message text."""
        assert _format_relay_connection_error(OSError(NETWORK_DOWN)) == NETWORK_DOWN


class TestSendError:
    """Tests for error response helpers."""

    async def test_sends_json_error(self) -> None:
        """Should send a JSON error message with the correct structure."""
        ws = AsyncMock()
        await _send_error(ws, MESSAGE_ID, 503, DETAIL)
        ws.send.assert_called_once()
        payload = json.loads(ws.send.call_args[0][0])
        assert payload["id"] == MESSAGE_ID
        assert payload["status"] == 503
        assert payload["data"]["detail"] == DETAIL


class TestWebsocketConnect:
    """Tests for direct websockets.asyncio connection usage."""

    async def test_run_forever_uses_websocket_connection_iterator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The relay service should delegate connection iteration to one helper."""
        runtime_state = RuntimeState()
        runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)
        service.configure(_noop_asgi_app)
        iter_connections = MagicMock(return_value=_ConnectionIterator([_Conn()]))
        monkeypatch.setattr(relay_mod, "_iter_websocket_connections", iter_connections)
        monkeypatch.setattr(relay_mod, "_receive_loop", AsyncMock(side_effect=asyncio.CancelledError()))

        with pytest.raises(asyncio.CancelledError):
            await service.run_forever()

        iter_connections.assert_called_once_with(
            EXAMPLE_RELAY_BACKEND_URL_WITH_CAMERA_ID.replace("cam-42", "cam-1"), runtime_state
        )

    async def test_connection_iterator_builds_fresh_auth_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each outer connection iterator attempt should mint a fresh device assertion."""
        connect = MagicMock(side_effect=[_Connect(_Conn()), _Connect(_Conn())])
        assertions = iter(["jwt-1", "jwt-2"])
        monkeypatch.setattr(relay_mod, "websocket_connect", connect)
        monkeypatch.setattr(relay_mod, "build_device_assertion", lambda *_: next(assertions))

        connections = _iter_websocket_connections(EXAMPLE_RELAY_BACKEND_URL, RuntimeState())

        assert await anext(connections) is not None
        assert await anext(connections) is not None

        assert connect.call_args_list[0].kwargs["additional_headers"] == {"Authorization": "Bearer jwt-1"}
        assert connect.call_args_list[1].kwargs["additional_headers"] == {"Authorization": "Bearer jwt-2"}
        assert connect.call_args_list[0].kwargs["max_size"] == RELAY_WS_TEXT_FRAME_LIMIT_BYTES
        assert connect.call_args_list[0].kwargs["max_queue"] == 8
        assert connect.call_args_list[0].kwargs["compression"] is None

    async def test_connection_iterator_uses_process_exception_and_refreshes_auth_after_transient_handshake_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transient handshake classification should retry with a new connect call and fresh JWT."""
        transient_response = Response(502, "Bad Gateway", Headers(), b"bad gateway")
        connect = MagicMock(side_effect=[_HandshakeContextFailure(transient_response), _Connect(_Conn())])
        assertions = iter(["jwt-failed", "jwt-retry"])
        monkeypatch.setattr(relay_mod, "websocket_connect", connect)
        monkeypatch.setattr(relay_mod, "build_device_assertion", lambda *_: next(assertions))
        monkeypatch.setattr(relay_mod, "_relay_reconnect_delays", lambda: iter([0.0]))
        sleep = AsyncMock()
        monkeypatch.setattr(relay_mod.asyncio, "sleep", sleep)

        connections = _iter_websocket_connections(EXAMPLE_RELAY_BACKEND_URL, RuntimeState())

        assert await anext(connections) is not None

        assert connect.call_args_list[0].kwargs["additional_headers"] == {"Authorization": "Bearer jwt-failed"}
        assert connect.call_args_list[1].kwargs["additional_headers"] == {"Authorization": "Bearer jwt-retry"}
        sleep.assert_awaited_once_with(0.0)


class TestReceiveLoop:
    """Tests for relay receive loop behavior."""

    async def test_ignores_binary_frames(self) -> None:
        """Should ignore binary frames without crashing."""
        ws = _Conn([b"\x00binary"])
        await _receive_loop(
            cast("ClientConnection", ws),
            asgi_app=_noop_asgi_app,
            relay_state=RelayRuntimeState(),
            runtime_state=RuntimeState(),
        )
        # Should not crash, just log and continue until connection closes

    async def test_ignores_invalid_json(self) -> None:
        """Should ignore frames that aren't valid JSON without crashing."""
        ws = _Conn(["not json {{{"])
        await _receive_loop(
            cast("ClientConnection", ws),
            asgi_app=_noop_asgi_app,
            relay_state=RelayRuntimeState(),
            runtime_state=RuntimeState(),
        )

    async def test_handles_ping(self) -> None:
        """Should respond to ping messages with a pong."""
        ws = _Conn([json.dumps({"type": "ping"})])
        await _receive_loop(
            cast("ClientConnection", ws),
            asgi_app=_noop_asgi_app,
            relay_state=RelayRuntimeState(),
            runtime_state=RuntimeState(),
        )
        # Should have sent a pong
        ws.send.assert_awaited_once()
        pong = json.loads(ws.send.await_args_list[0].args[0])
        assert pong["type"] == PONG_TYPE

    async def test_ignores_unknown_type(self) -> None:
        """Should ignore messages with unknown type without crashing."""
        ws = _Conn([json.dumps({"type": "unknown"})])
        await _receive_loop(
            cast("ClientConnection", ws),
            asgi_app=_noop_asgi_app,
            relay_state=RelayRuntimeState(),
            runtime_state=RuntimeState(),
        )

    async def test_ignores_non_object_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid JSON frames that are not objects should not dispatch commands."""
        ws = _Conn([json.dumps(["request"])])
        handler = AsyncMock()
        monkeypatch.setattr(relay_mod, "_handle_command", handler)

        await _receive_loop(
            cast("ClientConnection", ws),
            asgi_app=_noop_asgi_app,
            relay_state=RelayRuntimeState(),
            runtime_state=RuntimeState(),
        )

        handler.assert_not_awaited()

    async def test_dispatches_request_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should dispatch messages of type "request" to the command handler."""
        ws = _Conn([json.dumps({"type": "request", "id": "1"})])
        monkeypatch.setattr(relay_mod.asyncio, "create_task", lambda coro: asyncio.get_running_loop().create_task(coro))
        handler = AsyncMock()
        monkeypatch.setattr(relay_mod, "_handle_command", handler)
        await _receive_loop(
            cast("ClientConnection", ws),
            asgi_app=_noop_asgi_app,
            relay_state=RelayRuntimeState(),
            runtime_state=RuntimeState(),
        )
        await asyncio.sleep(0)
        handler.assert_awaited_once()

    async def test_rejects_request_when_pending_limit_is_reached(self, caplog: pytest.LogCaptureFixture) -> None:
        """Relay request floods should receive backpressure before spawning tasks."""
        ws = AsyncMock()
        http = AsyncMock()
        blocker = asyncio.Event()

        async def _wait_until_released() -> None:
            await blocker.wait()

        pending_task = asyncio.create_task(_wait_until_released())
        pending_tasks = {pending_task}
        try:
            with caplog.at_level(logging.WARNING):
                await _handle_relay_message(
                    ws,
                    http,
                    json.dumps({"type": "request", "id": OVERFLOW_MESSAGE_ID, "method": "GET", "path": CAMERA_PATH}),
                    pending_tasks,
                    pending_tasks.discard,
                    asyncio.Semaphore(1),
                    relay_state=RelayRuntimeState(),
                    max_pending_commands=1,
                )
        finally:
            blocker.set()
            await asyncio.gather(pending_task, return_exceptions=True)

        payload = json.loads(ws.send.call_args.args[0])
        assert payload["id"] == OVERFLOW_MESSAGE_ID
        assert payload["status"] == 429
        assert len(pending_tasks) == 1
        record = _log_record(caplog, SECURITY_RELAY_RATE_LIMIT_EVENT)
        assert record.outcome == "blocked"
        assert record.status_code == 429


class TestHandleCommand:
    """Tests for relay command dispatch."""

    async def test_malformed_request_does_not_reach_local_http_client(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Malformed relay requests should be rejected before local API dispatch."""
        ws = AsyncMock()
        http = AsyncMock()

        with caplog.at_level(logging.WARNING):
            await _handle_command(ws, http, {"type": "request", "id": MALFORMED_MESSAGE_ID})

        http.request.assert_not_called()
        payload = json.loads(ws.send.call_args.args[0])
        assert payload["id"] == MALFORMED_MESSAGE_ID
        assert payload["status"] == 400
        record = _log_record(caplog, SECURITY_RELAY_MALFORMED_EVENT)
        assert record.outcome == "failure"
        assert record.status_code == 400

    async def test_malformed_request_without_id_is_ignored(self) -> None:
        """Malformed relay requests without a usable id cannot receive an error response."""
        ws = AsyncMock()
        http = AsyncMock()

        await _handle_command(ws, http, {"type": "request"})

        http.request.assert_not_called()
        ws.send.assert_not_called()

    async def test_forwards_trace_headers_to_local_request(self) -> None:
        """Trace propagation headers should survive the relay hop."""

        def _handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["traceparent"] == TRACEPARENT
            assert request.headers["tracestate"] == TRACESTATE
            assert request.headers["baggage"] == BAGGAGE
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            ws = AsyncMock()
            await _handle_command(
                ws,
                http,
                {
                    "id": "msg-trace",
                    "method": "GET",
                    "path": LOCAL_ACCESS_PATH,
                    "headers": {
                        "TraceParent": TRACEPARENT,
                        "tracestate": TRACESTATE,
                        "Baggage": BAGGAGE,
                    },
                },
            )

        payload = json.loads(ws.send.call_args.args[0])
        assert payload["data"] == {"ok": True}

    async def test_handles_binary_response(self) -> None:
        """Should send binary responses as bytes frames."""

        def _binary_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"abc", headers={"content-type": "image/jpeg"})

        transport = httpx.MockTransport(_binary_handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            ws = AsyncMock()
            await _handle_command(ws, http, {"id": "msg-1", "method": "GET", "path": CAMERA_PATH})
            assert ws.send.await_count == 2
            ws.send.assert_any_await(b"abc")

    async def test_handles_hls_video_segment_response_as_binary(self) -> None:
        """HLS video segments should travel over the relay as binary frames."""

        def _hls_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=HLS_SEGMENT_BYTES, headers={"content-type": HLS_SEGMENT_CONTENT_TYPE})

        transport = httpx.MockTransport(_hls_handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            ws = AsyncMock()
            await _handle_command(
                ws,
                http,
                {"id": "msg-hls", "method": "GET", "path": PREVIEW_HLS_SEGMENT_PATH},
            )

        header = json.loads(ws.send.await_args_list[0].args[0])
        assert header["has_binary"] is True
        assert header["content_type"] == HLS_SEGMENT_CONTENT_TYPE
        ws.send.assert_any_await(HLS_SEGMENT_BYTES)

    async def test_concurrent_binary_responses_stay_adjacent(self) -> None:
        """A shared send_lock must keep each binary header next to its own body."""

        def _handler(request: httpx.Request) -> httpx.Response:
            body = request.url.params["tag"].encode()
            return httpx.Response(200, content=body, headers={"content-type": HLS_SEGMENT_CONTENT_TYPE})

        sent: list[object] = []

        class _YieldingConn:
            async def send(self, frame: object) -> None:
                sent.append(frame)
                await asyncio.sleep(0)  # force a scheduling point between frames

        lock = asyncio.Lock()
        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            ws = cast("ClientConnection", _YieldingConn())

            def _cmd(msg_id: str, tag: str) -> dict:
                return {"id": msg_id, "method": "GET", "path": PREVIEW_HLS_SEGMENT_PATH, "params": {"tag": tag}}

            await asyncio.gather(
                _handle_command(ws, http, _cmd("a", "aaa"), lock),
                _handle_command(ws, http, _cmd("b", "bbb"), lock),
            )

        # Every has_binary header must be immediately followed by its own bytes.
        for i, frame in enumerate(sent):
            if isinstance(frame, str) and json.loads(frame).get("has_binary"):
                tag = json.loads(frame)["id"] * 3  # id "a" -> b"aaa"
                assert sent[i + 1] == tag.encode()

    async def test_handles_text_response(self) -> None:
        """Should send text responses as JSON frames."""

        def _text_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(_text_handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            ws = AsyncMock()
            await _handle_command(ws, http, {"id": "msg-2", "method": "GET", "path": LOCAL_ACCESS_PATH})
            ws.send.assert_awaited_once()
            payload = json.loads(ws.send.call_args.args[0])
            assert payload["data"] == {"ok": True}

    async def test_local_dispatch_timeout_sends_504(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ASGITransport ignores httpx timeouts, so the explicit deadline must fire."""
        monkeypatch.setattr(relay_mod, "RELAY_COMMAND_TIMEOUT_SECONDS", 0.01)
        http = AsyncMock()

        async def _hang(*_args: object, **_kwargs: object) -> httpx.Response:
            await asyncio.sleep(5)
            unreachable = "unreachable"
            raise AssertionError(unreachable)

        http.request = _hang
        ws = AsyncMock()

        await _handle_command(ws, http, {"id": "msg-slow", "method": "GET", "path": CAMERA_PATH})

        payload = json.loads(ws.send.call_args.args[0])
        assert payload["status"] == 504
        assert payload["data"]["detail"] == "Local dispatch timed out."

    async def test_receive_loop_dispatches_command_through_asgi_app(self) -> None:
        """A relayed command must reach the bound ASGI app in-process and answer over the socket."""
        ws = _Conn([json.dumps({"type": "request", "id": "msg-asgi", "method": "GET", "path": CAMERA_PATH})])

        await _receive_loop(
            cast("ClientConnection", ws),
            asgi_app=_json_ok_asgi_app,
            relay_state=RelayRuntimeState(),
            runtime_state=RuntimeState(),
        )

        payload = json.loads(ws.send.await_args_list[-1].args[0])
        assert payload["id"] == "msg-asgi"
        assert payload["status"] == 200
        assert payload["data"] == {"ok": True}

    async def test_http_error_sends_503(self) -> None:
        """Transport errors should be translated into a 503 response."""
        http = AsyncMock()
        http.request = AsyncMock(side_effect=httpx.ConnectError(RELAY_COMMAND_ERROR))
        ws = AsyncMock()

        await _handle_command(ws, http, {"id": "msg-3", "method": "GET", "path": CAMERA_PATH})

        payload = json.loads(ws.send.call_args.args[0])
        assert payload["status"] == 503
        assert payload["data"]["detail"] == RELAY_COMMAND_ERROR

    async def test_403_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A 403 from the local API should emit a warning."""

        def _handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"ok": True})

        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            ws = AsyncMock()
            with caplog.at_level("WARNING"):
                await _handle_command(ws, http, {"id": "msg-4", "method": "GET", "path": CAMERA_PATH})
        assert RELAY_403_FRAGMENT in caplog.text

    async def test_blocks_unexpected_relay_command_path(self, caplog: pytest.LogCaptureFixture) -> None:
        """Relay commands should only reach known local API endpoints."""
        http = AsyncMock()
        ws = AsyncMock()

        with caplog.at_level(logging.WARNING):
            await _handle_command(
                ws,
                http,
                {"id": "msg-blocked", "method": BLOCKED_RELAY_METHOD, "path": BLOCKED_RELAY_PATH},
            )

        http.request.assert_not_called()
        payload = json.loads(ws.send.call_args.args[0])
        assert payload["status"] == 403
        assert payload["data"]["detail"] == BLOCKED_RELAY_DETAIL
        record = _log_record(caplog, SECURITY_RELAY_FORBIDDEN_EVENT)
        assert record.outcome == "failure"
        assert record.method == BLOCKED_RELAY_METHOD
        assert record.path == BLOCKED_RELAY_PATH
        assert record.status_code == 403

    async def test_blocks_relative_relay_command_path(self) -> None:
        """Relay commands should not dispatch relative paths into the local client."""
        http = AsyncMock()
        ws = AsyncMock()

        await _handle_command(
            ws,
            http,
            {"id": "msg-relative", "method": BLOCKED_RELAY_METHOD, "path": BLOCKED_RELATIVE_RELAY_PATH},
        )

        http.request.assert_not_called()
        payload = json.loads(ws.send.call_args.args[0])
        assert payload["status"] == 403
        assert payload["data"]["detail"] == BLOCKED_RELAY_DETAIL


class TestRelayServiceRunForever:
    """Tests for the runtime-owned relay loop."""

    async def test_returns_when_not_configured(self) -> None:
        """Should return immediately if the relay is not configured."""
        service = RelayService(state=RelayRuntimeState(), runtime_state=RuntimeState())
        await service.run_forever()

    async def test_reconnects_after_connection_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Closed relay connections should reconnect via the websockets async iterator."""
        runtime_state = RuntimeState()
        runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)
        service.configure(_noop_asgi_app)
        first = _Conn([ConnectionClosedOK(None, None)])
        second = _Conn([asyncio.CancelledError()])
        iter_connections = MagicMock(return_value=_ConnectionIterator([first, second]))
        monkeypatch.setattr(relay_mod, "_iter_websocket_connections", iter_connections)

        with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
            await service.run_forever()

        iter_connections.assert_called_once()
        assert RELAY_RECONNECT_LOG in caplog.text

    async def test_run_forever_delegates_reconnect_iteration_to_connection_iterator(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The service loop should rely on the connection iterator for reconnect attempts."""
        runtime_state = RuntimeState()
        runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)
        service.configure(_noop_asgi_app)
        iter_connections = MagicMock(return_value=_ConnectionIterator([_Conn([asyncio.CancelledError()])]))
        monkeypatch.setattr(relay_mod, "_iter_websocket_connections", iter_connections)

        with pytest.raises(asyncio.CancelledError):
            await service.run_forever()

        iter_connections.assert_called_once()

    async def test_fatal_handshake_rejection_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fatal websocket iterator failures should stop the relay loop."""
        runtime_state = RuntimeState()
        runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)
        service.configure(_noop_asgi_app)
        response = Response(401, "Unauthorized", Headers(), b"unauthorized")
        monkeypatch.setattr(
            relay_mod, "_iter_websocket_connections", lambda _url, _state: _HandshakeIteratorFailure(response)
        )

        with pytest.raises(InvalidStatus):
            await service.run_forever()


class _Conn:
    """Async iterator stand-in for websockets.asyncio ClientConnection."""

    def __init__(self, messages: list[str | bytes | BaseException] | None = None) -> None:
        self._messages = messages or []
        self.send = AsyncMock()

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        return self

    async def __anext__(self) -> str | bytes:
        if not self._messages:
            raise StopAsyncIteration
        message = self._messages.pop(0)
        if isinstance(message, StopAsyncIteration):
            raise message
        if isinstance(message, BaseException):
            raise message
        return message


class _Connect:
    """Async context manager stand-in for websockets.asyncio.client.connect."""

    def __init__(self, connection: _Conn) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Conn:
        return self._connection

    async def __aexit__(self, *_: object) -> None:
        return None


class _ConnectionIterator:
    """Async iterator stand-in for the relay connection iterator."""

    def __init__(self, connections: list[_Conn]) -> None:
        self._connections = connections

    def __aiter__(self) -> AsyncIterator[_Conn]:
        return self

    async def __anext__(self) -> _Conn:
        if not self._connections:
            raise StopAsyncIteration
        return self._connections.pop(0)


class _HandshakeContextFailure:
    """Async context manager that raises InvalidStatus during connect."""

    def __init__(self, response: Response) -> None:
        self._response = response

    async def __aenter__(self) -> _Conn:
        raise InvalidStatus(self._response)

    async def __aexit__(self, *_: object) -> None:
        return None


class _HandshakeIteratorFailure:
    """Async iterator that raises InvalidStatus before yielding a connection."""

    def __init__(self, response: Response) -> None:
        self._response = response

    def __aiter__(self) -> AsyncIterator[_Conn]:
        return self

    async def __anext__(self) -> _Conn:
        raise InvalidStatus(self._response)
