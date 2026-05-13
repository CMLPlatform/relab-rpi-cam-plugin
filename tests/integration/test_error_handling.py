"""Tests for client-safe unhandled error responses."""

import logging
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

ERROR_PATH = "/__tests/unhandled-error"
REQUEST_ID = "err-req-123"
SENSITIVE_FRAGMENT = "api_key=test-value"


def _ensure_error_route(app: FastAPI) -> None:
    if any(getattr(route, "path", None) == ERROR_PATH for route in app.routes):
        return

    @app.get(ERROR_PATH, include_in_schema=False)
    async def _raise_unhandled_error() -> None:
        msg = f"boom {SENSITIVE_FRAGMENT}"
        raise RuntimeError(msg)


async def test_unhandled_exception_response_is_safe_and_logs_request_id(
    test_app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unexpected errors should return safe client output and keep correlation metadata."""
    _ensure_error_route(test_app)
    transport = ASGITransport(app=test_app, raise_app_exceptions=False)

    with caplog.at_level(logging.ERROR):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(ERROR_PATH, headers={"X-Request-ID": REQUEST_ID})

    assert response.status_code == 500
    assert response.json() == {"detail": {"message": "Internal server error", "request_id": REQUEST_ID}}
    assert SENSITIVE_FRAGMENT not in response.text
    record = cast("Any", next(record for record in caplog.records if getattr(record, "event", "") == "error.unhandled"))
    assert record.outcome == "failure"
    assert record.status_code == 500
    assert record.request_id == REQUEST_ID
