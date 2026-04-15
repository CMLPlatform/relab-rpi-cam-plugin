"""Custom logging configuration for the app."""

import json
import logging
import sys
from datetime import UTC, datetime
from logging.config import dictConfig
from pathlib import Path

from app.core.config import settings


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


def setup_logging(log_level: str = "INFO", log_file: Path | str = settings.log_path / "app.log") -> None:
    """Logging setup with human-readable console output and structured JSON file logging."""
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
                "filename": str(log_file),
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
