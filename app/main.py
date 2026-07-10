"""FastAPI composition root for the Raspberry Pi camera streaming application."""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.__version__ import version
from app.camera.exceptions import CameraInitializationError
from app.core.headers import client_error_detail
from app.core.lifespan import lifespan
from app.core.middleware import register_middleware
from app.core.runtime import ensure_app_runtime
from app.core.settings import settings
from app.observability.logging import build_security_log_extra, setup_logging
from app.observability.tracing import setup_observability
from app.router import router as main_router

setup_logging()
logger = logging.getLogger(__name__)

_ALLOWED_STATIC_EXTENSIONS = frozenset({".css", ".ico", ".js", ".png"})


class AllowlistedStaticFiles(StaticFiles):
    """StaticFiles variant that only serves the app's expected asset types."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Serve allowlisted static asset extensions and hide everything else."""
        if Path(path).suffix.lower() not in _ALLOWED_STATIC_EXTENSIONS:
            raise StarletteHTTPException(status_code=404)
        return await super().get_response(path, scope)


app = FastAPI(
    lifespan=lifespan,
    version=version,
    title="Raspberry Pi Camera API",
    description=(
        "This API allows you to remotely capture images and stream video from a Raspberry Pi camera. "
        "It is used as a plugin for the ReLab platform."
        '<br>For more info, visit the <a href="https://github.com/CMLplatform/relab" target="_blank">ReLab GitHub</a>.'
    ),
    docs_url="/docs" if settings.api_docs_enabled else None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    redoc_url=None,
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
    logger.exception("Camera initialization failed", exc_info=(type(exc), exc, exc.__traceback__))
    return JSONResponse(
        status_code=500,
        content={"detail": client_error_detail("Camera initialization failed")},
    )


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Handle unexpected exceptions with client-safe output and full server logs."""
    logger.exception(
        "Unhandled application exception",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra=build_security_log_extra(
            event="error.unhandled",
            outcome="failure",
            request=request,
            status_code=500,
        ),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": client_error_detail(
                "Internal server error",
                request_id=getattr(request.state, "request_id", None),
            )
        },
    )


app.add_exception_handler(CameraInitializationError, camera_initialization_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.state.runtime = runtime

app.include_router(main_router)
app.mount("/static", AllowlistedStaticFiles(directory=settings.static_path), name="static")
