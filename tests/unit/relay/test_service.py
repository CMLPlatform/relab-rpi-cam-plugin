"""Tests for relay module helpers."""

import asyncio
import json
from typing import Self
from unittest.mock import AsyncMock

import httpx
import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from app.core.runtime_state import RuntimeState
from app.relay import service as relay_mod
from app.relay.service import (
    RelayService,
    _extract_trace_headers,
    _format_relay_connection_error,
    _handle_command,
    _receive_loop,
    _send_error,
)
from app.relay.state import RelayRuntimeState
from tests.constants import EXAMPLE_RELAY_BACKEND_URL, EXAMPLE_RELAY_BACKEND_URL_WITH_CAMERA_ID

RELAY_AUTH_SCHEME = "device_assertion"
RELAY_KEY_ID = "key-1"
RELAY_PRIVATE_KEY_PEM = "private-key"
MESSAGE_ID = "msg-1"
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
RELAY_RECONNECT_LOG = "Relay connection lost (HTTP 502). Reconnecting in 2s"
TRACEBACK_MARKER = "Traceback"


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
    """Tests for the websocket context manager wrapper."""

    async def test_closes_raw_websocket_on_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The wrapper should close the underlying websocket when the context exits."""
        raw_ws = AsyncMock()
        raw_ws.close = AsyncMock()
        monkeypatch.setattr(relay_mod.websockets, "connect", AsyncMock(return_value=raw_ws))
        monkeypatch.setattr(relay_mod, "build_device_assertion", lambda: "jwt")

        async with relay_mod._websocket_connect(EXAMPLE_RELAY_BACKEND_URL) as ws:
            assert ws is not None

        raw_ws.close.assert_awaited_once()


class TestReceiveLoop:
    """Tests for relay receive loop behavior."""

    async def test_ignores_binary_frames(self) -> None:
        """Should ignore binary frames without crashing."""
        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=[b"\x00binary", OSError("closed")])
        await _receive_loop(ws, relay_state=RelayRuntimeState(), runtime_state=RuntimeState())
        # Should not crash, just log and continue until connection closes

    async def test_ignores_invalid_json(self) -> None:
        """Should ignore frames that aren't valid JSON without crashing."""
        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=["not json {{{", OSError("closed")])
        await _receive_loop(ws, relay_state=RelayRuntimeState(), runtime_state=RuntimeState())

    async def test_handles_ping(self) -> None:
        """Should respond to ping messages with a pong."""
        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=[json.dumps({"type": "ping"}), OSError("closed")])
        await _receive_loop(ws, relay_state=RelayRuntimeState(), runtime_state=RuntimeState())
        # Should have sent a pong
        ws.send.assert_called_once()
        pong = json.loads(ws.send.call_args[0][0])
        assert pong["type"] == PONG_TYPE

    async def test_ignores_unknown_type(self) -> None:
        """Should ignore messages with unknown type without crashing."""
        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=[json.dumps({"type": "unknown"}), OSError("closed")])
        await _receive_loop(ws, relay_state=RelayRuntimeState(), runtime_state=RuntimeState())

    async def test_dispatches_request_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should dispatch messages of type "request" to the command handler."""
        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=[json.dumps({"type": "request", "id": "1"}), OSError("closed")])
        monkeypatch.setattr(relay_mod.asyncio, "create_task", lambda coro: asyncio.get_running_loop().create_task(coro))
        handler = AsyncMock()
        monkeypatch.setattr(relay_mod, "_handle_command", handler)
        await _receive_loop(ws, relay_state=RelayRuntimeState(), runtime_state=RuntimeState())
        await asyncio.sleep(0)
        handler.assert_awaited_once()


class TestHandleCommand:
    """Tests for relay command dispatch."""

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
                    "path": "/json",
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
            await _handle_command(ws, http, {"id": "msg-1", "method": "GET", "path": "/image"})
            assert ws.send.await_count == 1
            ws.send_bytes.assert_awaited_once_with(b"abc")

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
                {"id": "msg-hls", "method": "GET", "path": "/preview/hls/cam-preview/seg.mp4"},
            )

        header = json.loads(ws.send.call_args.args[0])
        assert header["has_binary"] is True
        assert header["content_type"] == HLS_SEGMENT_CONTENT_TYPE
        ws.send_bytes.assert_awaited_once_with(HLS_SEGMENT_BYTES)

    async def test_handles_text_response(self) -> None:
        """Should send text responses as JSON frames."""

        def _text_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(_text_handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            ws = AsyncMock()
            await _handle_command(ws, http, {"id": "msg-2", "method": "GET", "path": "/json"})
            ws.send.assert_awaited_once()
            payload = json.loads(ws.send.call_args.args[0])
            assert payload["data"] == {"ok": True}

    async def test_http_error_sends_503(self) -> None:
        """Transport errors should be translated into a 503 response."""
        http = AsyncMock()
        http.request = AsyncMock(side_effect=httpx.ConnectError(RELAY_COMMAND_ERROR))
        ws = AsyncMock()

        await _handle_command(ws, http, {"id": "msg-3", "method": "GET", "path": "/broken"})

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
                await _handle_command(ws, http, {"id": "msg-4", "method": "GET", "path": "/forbidden"})
        assert RELAY_403_FRAGMENT in caplog.text


class TestExtractTraceHeaders:
    """Tests for relay trace header filtering."""

    def test_keeps_only_supported_trace_headers(self) -> None:
        """The relay should forward tracing headers and ignore unrelated ones."""
        assert _extract_trace_headers(
            {
                "TraceParent": "parent",
                "TrAcEsTaTe": "state",
                "baggage": "bag",
                "Authorization": "Bearer no",
                "X-Whatever": "nope",
            },
        ) == {
            "traceparent": "parent",
            "tracestate": "state",
            "baggage": "bag",
        }

    def test_ignores_non_string_header_names_and_values(self) -> None:
        """Malformed header payloads should be dropped quietly."""
        assert _extract_trace_headers(
            {
                "traceparent": "parent",
                "baggage": 123,
                1: "value",
            },
        ) == {"traceparent": "parent"}


class TestRelayServiceRunForever:
    """Tests for the runtime-owned relay loop."""

    async def test_returns_when_not_configured(self) -> None:
        """Should return immediately if the relay is not configured."""
        service = RelayService(state=RelayRuntimeState(), runtime_state=RuntimeState())
        await service.run_forever()

    async def test_reconnects_after_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should attempt to reconnect after a failure with exponential backoff."""
        runtime_state = RuntimeState()
        runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)
        monkeypatch.setattr(relay_mod, "_receive_loop", AsyncMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(relay_mod.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))

        class _Conn:
            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

        monkeypatch.setattr(relay_mod, "_websocket_connect", lambda _url: _Conn())
        with pytest.raises(asyncio.CancelledError):
            await service.run_forever()

    async def test_reconnects_after_websocket_handshake_rejection(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A transient handshake rejection should be treated as reconnectable."""
        runtime_state = RuntimeState()
        runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)
        handshake_response = Response(
            502,
            "Bad Gateway",
            Headers(),
            b"error code: 502",
        )
        monkeypatch.setattr(
            relay_mod,
            "_websocket_connect",
            lambda _url: _HandshakeFailure(handshake_response),
        )
        monkeypatch.setattr(relay_mod.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))

        with pytest.raises(asyncio.CancelledError):
            await service.run_forever()

    async def test_expected_connection_errors_log_without_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Expected reconnectable failures should log concisely without traceback noise."""
        runtime_state = RuntimeState()
        runtime_state.set_relay_credentials(
            relay_backend_url=EXAMPLE_RELAY_BACKEND_URL,
            relay_camera_id="cam-1",
            relay_auth_scheme=RELAY_AUTH_SCHEME,
            relay_key_id=RELAY_KEY_ID,
            relay_private_key_pem=RELAY_PRIVATE_KEY_PEM,
        )
        service = RelayService(state=RelayRuntimeState(), runtime_state=runtime_state)
        response = Response(502, "Bad Gateway", Headers(), b"error code: 502")
        monkeypatch.setattr(relay_mod, "_websocket_connect", lambda _url: _HandshakeFailure(response))
        monkeypatch.setattr(relay_mod.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))

        with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
            await service.run_forever()

        assert RELAY_RECONNECT_LOG in caplog.text
        assert TRACEBACK_MARKER not in caplog.text


class _HandshakeFailure:
    """Async context manager that raises an InvalidStatus on enter."""

    def __init__(self, response: Response) -> None:
        self._response = response

    async def __aenter__(self) -> Self:
        raise InvalidStatus(self._response)

    async def __aexit__(self, *_: object) -> None:
        return None
