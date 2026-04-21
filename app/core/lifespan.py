"""FastAPI lifespan handler: runtime bootstrap, workers, and shutdown."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from relab_rpi_cam_models.camera import CameraMode

from app.__version__ import version
from app.camera.dependencies import check_stream_duration, check_stream_health
from app.camera.exceptions import CameraInitializationError
from app.core.config import bootstrap_runtime_state, settings
from app.core.runtime import AppRuntime, ensure_app_runtime
from app.observability.logging import configure_library_loggers
from app.utils.files import cleanup_images, setup_directory

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _log_startup_banner(runtime: AppRuntime) -> None:
    """Print a concise operator-facing summary to logs / journalctl.

    Useful when accessing the Pi over SSH without a browser.  The banner shows
    the current operating mode, the setup page URL, and a hint for retrieving
    the local API key from the command line.
    """
    base_url = urlparse(str(settings.base_url))
    setup_port = base_url.port or 8018
    setup_url = f"http://<this-ip>:{setup_port}/setup"

    if runtime.runtime_state.relay_enabled:
        mode_line = f"PAIRED      camera_id={runtime.runtime_state.relay_camera_id}"
    elif settings.pairing_backend_url:
        mode_line = "PAIRING     waiting for code to be claimed in the ReLab app"
    else:
        mode_line = "IDLE        set PAIRING_BACKEND_URL in .env to enable pairing"

    local_key_hint = "run:  just show-key" if runtime.runtime_state.local_api_key else "not yet generated"
    pairing_hint = "pairing code will appear below in a boxed log banner" if settings.pairing_backend_url else None
    pairing_hint_line = f"  Note     : {pairing_hint}\n" if pairing_hint else ""

    sep = "═" * 54
    banner = (
        f"\n{sep}\n"
        f"  ReLab RPi Camera  v{version}\n"
        f"  Setup    : {setup_url}\n"
        f"  Mode     : {mode_line}\n"
        f"{pairing_hint_line}"
        f"  Local key: {local_key_hint}\n"
        f"{sep}"
    )
    logger.info(banner)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Lifespan event handler for FastAPI application.

    Note that the camera is set up lazily to avoid unnecessary resource use.
    """
    # Re-apply our logger normalization after Uvicorn/FastAPI have finished bootstrapping.
    configure_library_loggers()
    runtime = ensure_app_runtime(app)

    bootstrap_runtime_state(runtime.runtime_state)
    _log_startup_banner(runtime)

    await setup_directory(settings.image_path)
    logger.info("Temporary file directories set up")

    if runtime.runtime_state.relay_enabled:
        runtime.create_task(runtime.relay_service.run_forever(), name="ws_relay")
        logger.info("WebSocket relay started")
    elif settings.pairing_backend_url:

        async def _on_paired() -> None:
            runtime.create_task(runtime.relay_service.run_forever(), name="ws_relay")
            logger.info("Pairing complete — WebSocket relay started")

        runtime.create_task(runtime.pairing_service.run_forever(_on_paired), name="pairing")
        runtime.pairing_service.log_mode_started()

    # Recurring cleanup and health checks.
    runtime.create_repeating_task(cleanup_images, seconds=settings.cleanup_interval_s, name="cleanup_images")
    runtime.create_repeating_task(
        lambda: check_stream_duration(runtime.camera_manager),
        seconds=settings.check_stream_interval_s,
        name="check_stream_duration",
    )
    runtime.create_repeating_task(
        lambda: check_stream_health(runtime.camera_manager),
        seconds=settings.check_stream_health_interval_s,
        name="check_stream_health",
    )
    logger.info("Recurring cleanup and health check tasks started")

    # Prime the persistent picamera2 pipeline. The lores preview encoder is
    # managed by PreviewSleeper — it starts the encoder when the relay goes
    # active and hibernates it after ``preview_hibernate_after_s`` seconds of
    # relay idleness. The sleeper also handles the "no camera attached" case
    # by skipping ticks until one appears.
    try:
        await runtime.camera_manager.setup_camera(CameraMode.VIDEO)
    except (CameraInitializationError, RuntimeError) as exc:
        logger.warning("Camera not primed at startup: %s", exc)

    runtime.start_thermal_governor()
    logger.info("Thermal governor started")

    runtime.start_preview_sleeper()
    logger.info("Preview sleeper started (hibernate_after=%ds)", settings.preview_hibernate_after_s)

    runtime.start_preview_thumbnail_worker()
    logger.info("Preview thumbnail worker started")

    runtime.start_upload_queue_worker()
    logger.info("Upload queue worker started")

    yield

    # Shutdown order: sleeper first (so it stops the encoder cleanly), then
    # thermal governor (it also touches the encoder), then the rest.
    await runtime.stop_runtime_workers()
    runtime.cancel_tasks()
    await runtime.wait_for_managed_tasks()

    await runtime.camera_manager.cleanup(force=True)
    if runtime.observability_handle is not None:
        runtime.observability_handle.shutdown(app)
    logger.info("Camera resources cleaned up")
