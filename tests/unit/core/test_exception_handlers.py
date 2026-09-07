"""Tests for the application-level exception handlers in `app.main`."""

import json
import logging

import pytest
from fastapi import Request

import app.main as main_mod
from app.camera.exceptions import CameraInitializationError

TRACEBACK_TEXT = "Traceback (most recent call last)"


def _raised(exc: Exception) -> Exception:
    """Return the exception with a real traceback, as a handler always receives it."""
    try:
        raise exc
    except type(exc) as caught:
        return caught


def _request() -> Request:
    """Build the minimum ASGI scope the handlers and security logging read."""
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/camera/still",
            "raw_path": b"/camera/still",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 51234),
            "server": ("127.0.0.1", 8018),
        }
    )


class TestExceptionHandlers:
    """The handlers must stay registered, hide internals, and log the traceback."""

    def test_handlers_are_registered_on_the_app(self) -> None:
        """A handler that is never wired up cannot sanitize anything."""
        assert main_mod.app.exception_handlers[CameraInitializationError] is (
            main_mod.camera_initialization_exception_handler
        )
        assert main_mod.app.exception_handlers[Exception] is main_mod.unhandled_exception_handler

    async def test_camera_initialization_handler_hides_the_cause_and_logs_it(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The client gets a stable message; the traceback stays server-side."""
        exc = _raised(CameraInitializationError(0, "/dev/video0 busy: pid 4242 holds the sensor"))

        with caplog.at_level(logging.ERROR):
            response = await main_mod.camera_initialization_exception_handler(_request(), exc)

        assert response.status_code == 500
        assert json.loads(bytes(response.body))["detail"]["message"] == "Camera initialization failed"
        assert "/dev/video0" not in bytes(response.body).decode()
        assert TRACEBACK_TEXT in caplog.text
        assert "/dev/video0 busy" in caplog.text

    async def test_unhandled_handler_hides_the_cause_and_logs_it(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unexpected errors must not leak their message to the client either."""
        exc = _raised(RuntimeError("connection string postgres://user:hunter2@db/relab"))

        with caplog.at_level(logging.ERROR):
            response = await main_mod.unhandled_exception_handler(_request(), exc)

        assert response.status_code == 500
        assert json.loads(bytes(response.body))["detail"]["message"] == "Internal server error"
        assert "hunter2" not in bytes(response.body).decode()
        assert TRACEBACK_TEXT in caplog.text
