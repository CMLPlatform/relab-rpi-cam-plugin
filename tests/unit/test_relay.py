"""Tests for relay module helpers."""

import asyncio
import json
from typing import Self
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import Settings
from app.utils import relay as relay_mod
from app.utils.relay import _build_relay_url, _handle_command, _receive_loop, _relay_configured, _send_error

RELAY_URL = "wss://example.com/ws?camera_id=cam-42"
MESSAGE_ID = "msg-1"
DETAIL = "oops"
PONG_TYPE = "pong"


class TestRelayConfigured:
    """Tests for relay configuration detection."""

    def test_returns_false_when_no_credentials(self) -> None:
        """Should return False if any required credential is missing."""
        with patch("app.utils.relay.settings", Settings()):
            assert _relay_configured() is False

    def test_returns_true_when_all_set(self) -> None:
        """Should return True if all required credentials are set."""
        s = Settings(
            relay_backend_url="wss://example.com/ws",
            relay_camera_id="cam-1",
            relay_api_key="key-1",
        )
        with patch("app.utils.relay.settings", s):
            assert _relay_configured() is True

    def test_returns_false_when_partial(self) -> None:
        """Should return False if only some credentials are set."""
        s = Settings(relay_backend_url="wss://example.com/ws", relay_camera_id="cam-1")
        with patch("app.utils.relay.settings", s):
            assert _relay_configured() is False


class TestBuildRelayUrl:
    """Tests for relay URL construction."""

    def test_builds_url_with_camera_id(self) -> None:
        """Should build the relay URL with the camera_id query parameter."""
        s = Settings(relay_backend_url="wss://example.com/ws/", relay_camera_id="cam-42")
        with patch("app.utils.relay.settings", s):
            url = _build_relay_url()
            assert url == RELAY_URL


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


class TestReceiveLoop:
    """Tests for relay receive loop behavior."""

    async def test_ignores_binary_frames(self) -> None:
        """Should ignore binary frames without crashing."""
        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=[b"\x00binary", Exception("closed")])
        await _receive_loop(ws)
        # Should not crash, just log and continue until connection closes

    async def test_ignores_invalid_json(self) -> None:
        """Should ignore frames that aren't valid JSON without crashing."""
        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=["not json {{{", Exception("closed")])
        await _receive_loop(ws)

    async def test_handles_ping(self) -> None:
        """Should respond to ping messages with a pong."""
        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=[json.dumps({"type": "ping"}), Exception("closed")])
        await _receive_loop(ws)
        # Should have sent a pong
        ws.send.assert_called_once()
        pong = json.loads(ws.send.call_args[0][0])
        assert pong["type"] == PONG_TYPE

    async def test_ignores_unknown_type(self) -> None:
        """Should ignore messages with unknown type without crashing."""
        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=[json.dumps({"type": "unknown"}), Exception("closed")])
        await _receive_loop(ws)

    async def test_dispatches_request_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should dispatch messages of type "request" to the command handler."""
        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=[json.dumps({"type": "request", "id": "1"}), Exception("closed")])
        monkeypatch.setattr(relay_mod.asyncio, "create_task", lambda coro: asyncio.get_running_loop().create_task(coro))
        handler = AsyncMock()
        monkeypatch.setattr(relay_mod, "_handle_command", handler)
        await _receive_loop(ws)
        await asyncio.sleep(0)
        handler.assert_awaited_once()


class TestHandleCommand:
    """Tests for relay command dispatch."""

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


class TestRunRelay:
    """Tests for the top-level relay loop."""

    async def test_returns_when_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return immediately if the relay is not configured."""
        monkeypatch.setattr(relay_mod, "_relay_configured", lambda: False)
        await relay_mod.run_relay()

    async def test_reconnects_after_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should attempt to reconnect after a failure with exponential backoff."""
        monkeypatch.setattr(relay_mod, "_relay_configured", lambda: True)
        monkeypatch.setattr(relay_mod, "_build_relay_url", lambda: "wss://example.com/ws?camera_id=cam-1")
        monkeypatch.setattr(relay_mod, "_receive_loop", AsyncMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(relay_mod.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))

        class _Conn:
            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

        monkeypatch.setattr(relay_mod, "_websocket_connect", lambda _url: _Conn())
        with pytest.raises(asyncio.CancelledError):
            await relay_mod.run_relay()
