"""Custom logging configuration for the app."""

import logging
from logging.config import dictConfig
from pathlib import Path

from app.core.config import settings


def setup_logging(log_level: str = "INFO", log_file: Path | str = settings.log_path / "app.log") -> None:
    """Simple logging setup with Rich console output and file logging."""
    # Create log directory if it doesn't exist
    settings.log_path.mkdir(exist_ok=True)

    date_fmt = "%Y-%m-%d %H:%M:%S"

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console_format": {"format": "%(name)s: %(message)s", "datefmt": date_fmt},
            "file_format": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "datefmt": date_fmt},
        },
        "handlers": {
            "console": {
                "class": "rich.logging.RichHandler",
                "level": log_level.upper(),
                "formatter": "console_format",
                "rich_tracebacks": True,
                "tracebacks_show_locals": True,
            },
            "file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": str(log_file),
                "when": "midnight",
                "backupCount": 7,
                "encoding": "utf-8",
                "utc": True,
                "level": log_level.upper(),
                "formatter": "file_format",
            },
        },
        "loggers": {
            "": {
                "level": log_level.upper(),
                "handlers": ["console", "file"],
                "propagate": True,
            }
        },
    }

    dictConfig(config)

    # Set log level of noisy loggers to warning
    for logger_name in ["watchfiles.main"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    # Ensure uvicorn loggers propagate to root
    for logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastapi_cloud_cli", "fastapi_cli"]:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()  # Remove existing handlers
        logger.propagate = True  # Let root logger handle it
