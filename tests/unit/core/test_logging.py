"""Tests for request-scoped structured logging."""

from __future__ import annotations

import json
import logging
import sys

from fastapi import Request, Response

import app.core.middleware as middleware_mod
from app.core.runtime import AppRuntime, set_active_runtime
from app.observability import logging as logging_mod

LOG_MESSAGE = "hello world"
REQUEST_ID = "req-123"
CAMERA_ID = "cam-1"
STREAM_MODE = "youtube"
CLIENT_REQUEST_ID = "client-req-42"
SENSITIVE_SAMPLE = "sample-sensitive-value-1234567890"
REDACTED = "[REDACTED]"
BEARER_VALUE = "Bearer token-123"
PAIRING_CODE = "ABC123"
LOCAL_OPERATOR_ONLY_MESSAGE = "[local operator message omitted]"


def _request_with_headers(*, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/test",
        "raw_path": b"/test",
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }
    return Request(scope)


def _raise_sensitive_error() -> None:
    msg = f"stream_key={SENSITIVE_SAMPLE}"
    raise RuntimeError(msg)


class TestJsonFormatter:
    """Tests for structured JSON log formatting."""

    def test_includes_request_id_from_context_and_structured_fields(self) -> None:
        """Formatter output should include contextual request and stream fields."""
        formatter = logging_mod.JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        record.camera_id = CAMERA_ID
        record.stream_mode = STREAM_MODE

        token = logging_mod.bind_request_id(REQUEST_ID)
        try:
            payload = json.loads(formatter.format(record))
        finally:
            logging_mod.reset_request_id(token)

        assert payload["message"] == LOG_MESSAGE
        assert payload["request_id"] == REQUEST_ID
        assert payload["camera_id"] == CAMERA_ID
        assert payload["stream_mode"] == STREAM_MODE

    def test_redacts_sensitive_message_fields(self) -> None:
        """Structured logs should redact known secret fields before JSON output."""
        formatter = logging_mod.JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg='upload failed with {"s3_secret_access_key": "%s"} and Authorization: Bearer token-123',
            args=(SENSITIVE_SAMPLE,),
            exc_info=None,
        )

        payload = json.loads(formatter.format(record))

        assert SENSITIVE_SAMPLE not in payload["message"]
        assert BEARER_VALUE not in payload["message"]
        assert REDACTED in payload["message"]

    def test_redacts_exception_text(self) -> None:
        """Structured logs should redact secrets embedded in exception text."""
        formatter = logging_mod.JsonFormatter()
        try:
            _raise_sensitive_error()
        except RuntimeError:
            record = logging.LogRecord(
                name="test.logger",
                level=logging.ERROR,
                pathname=__file__,
                lineno=10,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )

        payload = json.loads(formatter.format(record))

        assert SENSITIVE_SAMPLE not in payload["exception"]
        assert REDACTED in payload["exception"]

    def test_local_operator_only_messages_are_omitted_from_json_logs(self) -> None:
        """Pairing banners may stay on console but must not enter shipped JSON logs."""
        formatter = logging_mod.JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg=f"PAIRING CODE: {PAIRING_CODE}",
            args=(),
            exc_info=None,
        )
        record.local_operator_only = True

        payload = json.loads(formatter.format(record))

        assert PAIRING_CODE not in payload["message"]
        assert payload["message"] == LOCAL_OPERATOR_ONLY_MESSAGE

    def test_console_formatter_redacts_sensitive_values(self) -> None:
        """Human-readable logs should share the same redaction policy."""
        formatter = logging_mod.RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="api_key=%s",
            args=(SENSITIVE_SAMPLE,),
            exc_info=None,
        )

        text = formatter.format(record)

        assert SENSITIVE_SAMPLE not in text
        assert REDACTED in text

    def test_build_log_extra_prefers_explicit_values(self) -> None:
        """Explicit log fields should win over runtime-derived fallbacks."""
        runtime = AppRuntime()
        runtime.runtime_state.relay_camera_id = "relay-cam"
        set_active_runtime(runtime)
        try:
            extra = logging_mod.build_log_extra(camera_id="explicit-cam", stream_mode=STREAM_MODE)
        finally:
            set_active_runtime(None)

        assert extra == {"camera_id": "explicit-cam", "stream_mode": STREAM_MODE}


class TestRequestContextMiddleware:
    """Tests for request-id context propagation middleware."""

    async def test_sets_request_id_context_and_response_header(self) -> None:
        """Middleware should bind a generated request id for the request lifetime."""
        seen: dict[str, str | None] = {}

        async def _call_next(_request: Request) -> Response:
            seen["request_id"] = logging_mod.get_request_id()
            return Response("ok")

        response = await middleware_mod.request_context_middleware(_request_with_headers(), _call_next)

        assert seen["request_id"] is not None
        assert response.headers["X-Request-ID"] == seen["request_id"]
        assert logging_mod.get_request_id() is None

    async def test_preserves_incoming_request_id(self) -> None:
        """Middleware should preserve a client-supplied request id header."""
        seen: dict[str, str | None] = {}

        async def _call_next(_request: Request) -> Response:
            seen["request_id"] = logging_mod.get_request_id()
            return Response("ok")

        response = await middleware_mod.request_context_middleware(
            _request_with_headers(headers=[(b"x-request-id", CLIENT_REQUEST_ID.encode())]),
            _call_next,
        )

        assert seen["request_id"] == CLIENT_REQUEST_ID
        assert response.headers["X-Request-ID"] == CLIENT_REQUEST_ID
