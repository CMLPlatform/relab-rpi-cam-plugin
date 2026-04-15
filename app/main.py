"""Main module for the Raspberry Pi camera streaming application."""

import logging
import time
from collections import defaultdict
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from relab_rpi_cam_models.camera import CameraMode
from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.__version__ import version
from app.api.dependencies.camera_management import (
    check_stream_duration,
    check_stream_health,
)
from app.api.exceptions import CameraInitializationError
from app.api.routers.main import router as main_router
from app.api.routers.setup import router as setup_router
from app.core.config import bootstrap_runtime_state, settings
from app.core.runtime import AppRuntime, ensure_app_runtime
from app.utils.files import cleanup_images, setup_directory
from app.utils.logging import (
    bind_request_id,
    configure_library_loggers,
    new_request_id,
    reset_request_id,
    setup_logging,
)
from app.utils.observability import setup_observability

setup_logging()
logger = logging.getLogger(__name__)

# Rate limit constants used by the rate limiter middleware
RATE_LIMIT_METHOD = "POST"
RATE_LIMIT_PATH = "/auth/login"
_SETUP_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self' http: https: ws: wss:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_DEFAULT_CSP = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
_HTTPS_SCHEME = "https"
_HTTP_SCOPE_TYPE = "http"
_HTTP_RESPONSE_START = "http.response.start"


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
    # Print operator-facing startup summary to logs / stdout
    _log_startup_banner(runtime)

    # Set up temporary directories
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

    # Start recurring cleanup and health check tasks. The lores preview encoder
    # runs for the lifetime of the app process, so there's no idle-state
    # release to schedule here.
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
    # managed by the PreviewSleeper below — it starts the encoder when the
    # relay goes active and hibernates it after ``preview_hibernate_after_s``
    # seconds of relay idleness. The sleeper also handles the "no camera
    # attached" case by skipping ticks until one appears.
    try:
        await runtime.camera_manager.setup_camera(CameraMode.VIDEO)
    except (CameraInitializationError, RuntimeError) as exc:
        # Don't crash the app if there's no camera attached — the API still
        # comes up for pairing, telemetry, etc. Image captures will fail with
        # their own errors.
        logger.warning("Camera not primed at startup: %s", exc)

    # Start the thermal governor. It watches CPU temperature and dynamically
    # drops the lores preview encoder bitrate when the SoC runs hot.
    runtime.start_thermal_governor()
    logger.info("Thermal governor started")

    # Start the preview sleeper. It polls every ~15s and decides whether the
    # lores encoder should be running based on relay connectivity + activity.
    runtime.start_preview_sleeper()
    logger.info(
        "Preview sleeper started (hibernate_after=%ds)",
        settings.preview_hibernate_after_s,
    )

    runtime.start_upload_queue_worker()
    logger.info("Upload queue worker started")

    yield

    # Shutdown order: sleeper first (so it stops the encoder cleanly), then
    # thermal governor (it also touches the encoder), then the rest.
    await runtime.stop_runtime_workers()
    runtime.cancel_tasks()
    await runtime.wait_for_managed_tasks()

    # Cleanup camera resources
    await runtime.camera_manager.cleanup(force=True)
    if runtime.observability_handle is not None:
        runtime.observability_handle.shutdown(app)
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

runtime = ensure_app_runtime(app)
runtime.observability_handle = setup_observability(
    app,
    enabled=settings.otel_enabled,
    service_name=settings.otel_service_name,
    otlp_endpoint=settings.otel_exporter_otlp_endpoint,
)

# Add rate limiting middleware first (outermost) for brute force protection
# Use function-based middleware backed by a single `RateLimiter` instance to
# avoid signature/type mismatches with BaseHTTPMiddleware.dispatch.
_rate_limiter = RateLimiter()


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next: Callable) -> Response:
    """Middleware to apply rate limiting on specific endpoints."""
    return await _rate_limiter.handle(request, call_next)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next: Callable) -> Response:
    """Attach a request id to the current context and echo it to the client."""
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    token = bind_request_id(request_id)
    try:
        response = await call_next(request)
    finally:
        reset_request_id(token)

    response.headers["X-Request-ID"] = request_id
    return response


def _content_security_policy_for_path(path: str) -> str:
    """Return the appropriate CSP for the requested route."""
    if path in {"/", "/setup"}:
        return _SETUP_CSP
    return _DEFAULT_CSP


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next: Callable) -> Response:
    """Attach baseline security headers to every HTTP response."""
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", _content_security_policy_for_path(request.url.path))
    if settings.base_url.scheme == _HTTPS_SCHEME:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# Add CORS middleware.
#
# When local_mode_enabled (default: True) the Pi accepts requests from any
# browser origin with allow_origins=["*"]. This is the correct policy for a
# local-network API that authenticates with X-API-Key headers (not cookies):
#   - Wildcard + allow_credentials=False is browser-compatible
#   - Physical LAN access + the API key (delivered via relay) is the auth boundary
#   - The setup HTML page is always opened directly (same-origin), so no CORS needed
#
# When local_mode_enabled=False (opt-out) the middleware falls back to the
# explicit allow-list defined in settings, keeping the relay-only security model.
if settings.local_mode_enabled:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "PATCH", "PUT"],
        allow_headers=["Content-Type", "Authorization", "Accept", settings.auth_key_name],
    )
else:
    _cors_origins = [str(origin).rstrip("/") for origin in settings.allowed_cors_origins]
    _cors_origins += [o.rstrip("/") for o in settings.local_allowed_origins]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "PATCH", "PUT"],
        allow_headers=["Content-Type", "Authorization", "Accept", settings.auth_key_name],
    )


class _PrivateNetworkAccessMiddleware:
    """Add Access-Control-Allow-Private-Network: true to every response.

    Chrome's Private Network Access (PNA) spec requires this header on both
    preflight and regular responses when an HTTPS page fetches resources from a
    private-network host (e.g. a LAN IP).  Without it, once Chrome enforces PNA
    fully, requests from ``https://app.cml-relab.org`` to the Pi's HTTP API will
    be blocked.  Browsers currently warn (``Local Network Access detected``) but
    do not block — this header suppresses the warnings and future-proofs the app.

    Must be registered AFTER CORSMiddleware so it wraps it (last added =
    outermost in Starlette's middleware stack), ensuring the header is appended
    to CORS preflight (OPTIONS) responses as well as regular responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != _HTTP_SCOPE_TYPE or not settings.local_mode_enabled:
            await self.app(scope, receive, send)
            return

        async def _send_with_pna(message: Message) -> None:
            if message["type"] == _HTTP_RESPONSE_START:
                headers = MutableHeaders(scope=message)
                headers.append("Access-Control-Allow-Private-Network", "true")
            await send(message)

        await self.app(scope, receive, _send_with_pna)


if settings.local_mode_enabled:
    app.add_middleware(_PrivateNetworkAccessMiddleware)


# Exception handlers
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

# Initialise runtime state attributes before the lifespan runs so cold-path
# requests and tests can resolve the same process services consistently.
app.state.runtime = runtime

# Include routers
app.include_router(main_router)
app.include_router(setup_router)  # No auth: setup page must be publicly accessible
app.mount("/static", StaticFiles(directory=settings.static_path), name="static")
