"""FastAPI composition root for the Raspberry Pi camera streaming application."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.__version__ import version
from app.camera.exceptions import CameraInitializationError
from app.core.config import settings
from app.core.lifespan import lifespan
from app.core.middleware import register_middleware
from app.core.runtime import ensure_app_runtime
from app.observability.logging import setup_logging
from app.observability.tracing import setup_observability
from app.router import router as main_router

setup_logging()
logger = logging.getLogger(__name__)


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

runtime = ensure_app_runtime(app)
runtime.observability_handle = setup_observability(
    app,
    enabled=settings.otel_enabled,
    service_name=settings.otel_service_name,
    otlp_endpoint=settings.otel_exporter_otlp_endpoint,
)

register_middleware(app)


async def camera_initialization_exception_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    """Handle camera initialization errors."""
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )


app.add_exception_handler(CameraInitializationError, camera_initialization_exception_handler)

app.state.runtime = runtime

app.include_router(main_router)
app.mount("/static", StaticFiles(directory=settings.static_path), name="static")
