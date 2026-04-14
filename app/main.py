"""Main module for the Raspberry Pi camera streaming application."""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app.__version__ import version
from app.api.dependencies.camera_management import (
    camera_manager,
    camera_to_standby,
    check_stream_duration,
    check_stream_health,
)
from app.api.exceptions import CameraInitializationError
from app.api.routers.main import router as main_router
from app.api.routers.setup import router as setup_router
from app.core.config import apply_relay_credentials, settings
from app.utils.files import cleanup_images, setup_directory
from app.utils.logging import configure_library_loggers, setup_logging
from app.utils.pairing import log_pairing_mode_started, run_pairing
from app.utils.relay import run_relay
from app.utils.tasks import repeat_task
from app.utils.thermal_governor import get_thermal_governor

setup_logging()
logger = logging.getLogger(__name__)

# Rate limit constants used by the rate limiter middleware
RATE_LIMIT_METHOD = "POST"
RATE_LIMIT_PATH = "/auth/login"


class RateLimiter:
    """Simple rate limiter for brute force protection on /auth/login.

    Implemented as a plain helper class. The actual middleware is registered
    with `@app.middleware("http")` below to avoid subclass signature/type
    mismatch with Starlette's `BaseHTTPMiddleware.dispatch`.
    """

    # Max failed login attempts per IP before rate limiting
    MAX_ATTEMPTS = 5
    # Time window for tracking attempts (seconds)
    WINDOW_SIZE = 300  # 5 minutes
    # Block duration after exceeding limits (seconds)
    BLOCK_DURATION = 300  # 5 minutes
    # Maximum tracked IPs to prevent unbounded memory growth
    MAX_TRACKED_IPS = 1000

    def __init__(self) -> None:
        # Track failed attempts: {ip: [(timestamp, is_failed), ...]}
        self._attempts: dict[str, list[tuple[float, bool]]] = defaultdict(list)

    def _sweep_stale_entries(self, now: float) -> None:
        """Remove entries with no attempts within the time window."""
        stale_ips = [
            ip for ip, attempts in self._attempts.items() if all(now - ts >= self.WINDOW_SIZE for ts, _ in attempts)
        ]
        for ip in stale_ips:
            del self._attempts[ip]

    async def handle(self, request: Request, call_next: Callable) -> Response:
        """Check rate limits before passing request to the app."""
        # Only rate limit the configured method/path
        if request.method != RATE_LIMIT_METHOD or request.url.path != RATE_LIMIT_PATH:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # Clean old attempts outside the window
        now = time.time()
        if client_ip in self._attempts:
            self._attempts[client_ip] = [
                (ts, failed) for ts, failed in self._attempts[client_ip] if now - ts < self.WINDOW_SIZE
            ]

        # Periodic sweep: evict stale IPs when the dict grows too large
        if len(self._attempts) > self.MAX_TRACKED_IPS:
            self._sweep_stale_entries(now)

        # Check if client is currently blocked
        attempts = self._attempts[client_ip]
        if attempts:
            failed_count = sum(1 for _, failed in attempts if failed)
            if failed_count >= self.MAX_ATTEMPTS:
                # Check if the most recent block is still active
                last_failed_time = max(ts for ts, failed in attempts if failed)
                if now - last_failed_time < self.BLOCK_DURATION:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Too many failed login attempts. Try again later."},
                    )

        # Call the endpoint
        response = await call_next(request)

        # Track the attempt result (check response status for failure)
        is_failed = response.status_code >= 400
        self._attempts[client_ip].append((now, is_failed))

        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:  # noqa: ARG001 # 'app' is expected by function signature
    """Lifespan event handler for FastAPI application.

    Note that the camera is set up lazily to avoid unnecessary resource use.
    """
    # Re-apply our logger normalization after Uvicorn/FastAPI have finished bootstrapping.
    configure_library_loggers()

    # Load relay credentials from pairing file (if present)
    apply_relay_credentials()

    # Set up temporary directories
    await setup_directory(settings.image_path)
    logger.info("Temporary file directories set up")

    # Start WebSocket relay or pairing mode
    background_tasks: set[asyncio.Task[None]] = set()

    if settings.relay_enabled:
        background_tasks.add(asyncio.create_task(run_relay(), name="ws_relay"))
        logger.info("WebSocket relay started")
    elif settings.pairing_backend_url:

        async def _on_paired() -> None:
            background_tasks.add(asyncio.create_task(run_relay(), name="ws_relay"))
            logger.info("Pairing complete — WebSocket relay started")

        background_tasks.add(asyncio.create_task(run_pairing(_on_paired), name="pairing"))
        log_pairing_mode_started()

    # Start recurring cleanup and health check tasks
    recurring_tasks = {
        repeat_task(cleanup_images, settings.cleanup_interval_s, "cleanup_images"),
        repeat_task(camera_to_standby, settings.camera_standby_s, "camera_to_standby"),
        repeat_task(check_stream_duration, settings.check_stream_interval_s, "check_stream_duration"),
        repeat_task(check_stream_health, settings.check_stream_health_interval_s, "check_stream_health"),
    }
    logger.info("Recurring cleanup and health check tasks started")

    # Start the thermal governor. It watches CPU temperature and dynamically
    # drops the lores preview encoder bitrate when the SoC runs hot.
    thermal_governor = get_thermal_governor()
    thermal_governor.start(camera_getter=lambda: camera_manager.backend.camera)
    logger.info("Thermal governor started")

    yield

    # Shutdown background services (thermal governor first so it stops touching the camera).
    await thermal_governor.stop()

    # Shutdown all background and recurring tasks
    all_tasks = background_tasks | recurring_tasks
    for task in all_tasks:
        task.cancel()
    await asyncio.gather(
        *(task for task in all_tasks if isinstance(task, asyncio.Task)),
        return_exceptions=True,
    )

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

# Add rate limiting middleware first (outermost) for brute force protection
# Use function-based middleware backed by a single `RateLimiter` instance to
# avoid signature/type mismatches with BaseHTTPMiddleware.dispatch.
_rate_limiter = RateLimiter()


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next: Callable) -> Response:
    """Middleware to apply rate limiting on specific endpoints."""
    return await _rate_limiter.handle(request, call_next)


# Add CORS middleware to allow requests from the main API host
app.add_middleware(
    CORSMiddleware,
    # CORS origins cannot have trailing slashes
    allow_origins=[str(origin).rstrip("/") for origin in settings.allowed_cors_origins],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    # Only allow necessary headers for security
    allow_headers=["Content-Type", "Authorization", "Accept", settings.auth_key_name],
)


# Exception handlers
async def camera_initialization_exception_handler(
    request: Request,  # noqa: ARG001 # Expected by FastAPI Exception Handler signature
    exc: Exception,
) -> JSONResponse:
    """Handle camera initialization errors."""
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )


app.add_exception_handler(CameraInitializationError, camera_initialization_exception_handler)

# Include routers
app.include_router(main_router)
app.include_router(setup_router)  # No auth: setup page must be publicly accessible
app.mount("/static", StaticFiles(directory=settings.static_path), name="static")
