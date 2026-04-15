"""Custom logging configuration for the app."""

import contextvars
import importlib
import json
import logging
import sys
from datetime import UTC, datetime
from logging.config import dictConfig
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from app.core.runtime_context import get_active_runtime

if TYPE_CHECKING:
    from app.core.config import Settings

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def _get_settings() -> "Settings":
    """Load static config lazily to avoid startup-time import cycles."""
    return importlib.import_module("app.core.config").settings


def new_request_id() -> str:
    """Generate a short request id for correlating logs."""
    return uuid4().hex


def get_request_id() -> str | None:
    """Return the current request id from context, if any."""
    return _request_id_var.get()


def bind_request_id(request_id: str) -> contextvars.Token[str | None]:
    """Bind a request id to the current context."""
    return _request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    """Reset the request id context to its prior value."""
    _request_id_var.reset(token)


def build_log_extra(
    *,
    camera_id: str | None = None,
    stream_mode: object | None = None,
) -> dict[str, object]:
    """Build common structured log fields for plugin events."""
    extra: dict[str, object] = {}
    runtime_camera_id: str | None = None
    try:
        runtime_camera_id = get_active_runtime().runtime_state.relay_camera_id or None
    except RuntimeError:
        runtime_camera_id = None
    resolved_camera_id = camera_id or runtime_camera_id
    if resolved_camera_id:
        extra["camera_id"] = resolved_camera_id
    if stream_mode is not None:
        extra["stream_mode"] = str(stream_mode)
    return extra


class JsonFormatter(logging.Formatter):
    """JSON log formatter for structured, machine-readable file output."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record into a JSON string."""
        log_entry: dict[str, object] = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if request_id := getattr(record, "request_id", None) or get_request_id():
            log_entry["request_id"] = request_id
        if camera_id := getattr(record, "camera_id", None):
            log_entry["camera_id"] = camera_id
        if stream_mode := getattr(record, "stream_mode", None):
            log_entry["stream_mode"] = str(stream_mode)
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            log_entry["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(log_entry, ensure_ascii=False)


def configure_library_loggers() -> None:
    """Normalize third-party loggers so app output stays readable."""
    root_handlers = logging.getLogger().handlers.copy()

    passthrough_loggers = [
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi_cloud_cli",
        "fastapi_cli",
    ]
    for logger_name in passthrough_loggers:
        logger = logging.getLogger(logger_name)
        logger.handlers = root_handlers.copy()
        logger.propagate = False

    for logger_name in ["httpx", "httpcore", "watchfiles.main"]:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)


def setup_logging(log_level: str = "INFO", log_file: Path | str | None = None) -> None:
    """Logging setup with human-readable console output and structured JSON file logging."""
    settings = _get_settings()
    resolved_log_file = log_file or (settings.log_path / "app.log")
    # Create log directory if it doesn't exist
    settings.log_path.mkdir(exist_ok=True)

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console_format": {
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "json_format": {
                "()": JsonFormatter,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "level": log_level.upper(),
                "formatter": "console_format",
            },
            "file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": str(resolved_log_file),
                "when": "midnight",
                "backupCount": 7,
                "encoding": "utf-8",
                "utc": True,
                "level": log_level.upper(),
                "formatter": "json_format",
            },
        },
        "loggers": {
            "": {
                "level": log_level.upper(),
                "handlers": ["console", "file"],
                "propagate": False,
            }
        },
    }

    dictConfig(config)
    configure_library_loggers()
