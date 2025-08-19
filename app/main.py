"""Main module for the Raspberry Pi camera streaming application."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.__version__ import version
from app.api.dependencies.camera_management import camera_manager, camera_to_standby, check_stream_duration
from app.api.routers.main import router as main_router
from app.core.config import settings
from app.utils.files import cleanup_images, setup_directory
from app.utils.tasks import repeat_task

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:  # noqa: ARG001 # 'app' is expected by function signature
    """Lifespan event handler for FastAPI application.

    Note that the camera is set up lazily to avoid unnecessary resource use.
    """
    # Set up temporary directories
    await setup_directory(settings.hls_path)
    await setup_directory(settings.image_path)
    logger.info("Temporary file directories set up")

    # Start recurring cleanup tasks
    recurring_tasks = {
        repeat_task(cleanup_images, settings.cleanup_interval_s, "cleanup_images"),
        repeat_task(camera_to_standby, settings.camera_standby_s, "camera_to_standby"),
        repeat_task(check_stream_duration, settings.check_stream_interval_s, "check_stream_duration"),
    }
    logger.info("Recurring cleanup tasks started")
    yield

    # Shutdown recurring tasks
    for task in recurring_tasks:
        task.cancel()

    # Cleanup camera resources
    await camera_manager.cleanup(force=True)
    logger.info("Camera resources cleaned up")


app = FastAPI(
    lifespan=lifespan,
    version=version,
    title="Raspberry Pi Camera API",
    description=(
        "This API allows you to remotely capture images and stream video from a Raspberry Pi camera. "
        "It is used as a plugin for the RELab platform."
        '<br>For more info, visit the <a href="https://github.com/CMLplatform/relab" target="_blank"> RELab GitHub</a>.'
    ),
)
# Add CORS middleware to allow requests from the main API host
app.add_middleware(
    CORSMiddleware,
    # CORS origins cannot have trailing slashes
    allow_origins=[str(origin).rstrip("/") for origin in settings.allowed_cors_origins],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# Include routers
app.include_router(main_router)
