# Build stage: compile dependencies
FROM ghcr.io/astral-sh/uv:0.11-python3.13-trixie-slim AS builder

ARG WORKDIR=/app
WORKDIR $WORKDIR

# uv optimizations
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Copy dependency files and project metadata
COPY .python-version pyproject.toml uv.lock README.md ./

# Copy shared model files
COPY relab_rpi_cam_models/ relab_rpi_cam_models/

# Add Raspberry Pi OS repository for picamera2 with GPG signature verification
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eux; \
    apt update && \
    apt install -y --no-install-recommends curl gnupg && \
    curl -fsSL https://archive.raspberrypi.org/debian/raspberrypi.gpg.key | \
    gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] http://archive.raspberrypi.org/debian/ trixie main" > /etc/apt/sources.list.d/raspi.list && \
    apt update && \
    apt install -y --no-install-recommends python3-picamera2 && \
    apt remove -y curl gnupg && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies to venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --system-site-packages && \
    uv sync --locked --no-install-project --no-editable --no-dev

# Copy application directory
COPY app/ app/

# Final sync with project code
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev

# Runtime stage: minimal final image
FROM python:3.13-slim-trixie

ARG WORKDIR=/app
WORKDIR $WORKDIR

# Install only runtime system dependencies and add Raspberry Pi OS repository for picamera2
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eux; \
    apt update && \
    apt install -y --no-install-recommends \
    curl \
    dbus \
    ffmpeg \
    gnupg \
    pulseaudio && \
    curl -fsSL https://archive.raspberrypi.org/debian/raspberrypi.gpg.key | \
    gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] http://archive.raspberrypi.org/debian/ trixie main" > /etc/apt/sources.list.d/raspi.list && \
    apt update && \
    apt install -y --no-install-recommends python3-picamera2 && \
    apt remove -y gnupg && \
    rm -rf /var/lib/apt/lists/*

# Set Python variables
ENV PYTHONPATH="$WORKDIR:/usr/lib/python3/dist-packages" \
    PYTHONUNBUFFERED=1 \
    PATH="$WORKDIR/.venv/bin:$PATH"

# Copy venv from builder
COPY --from=builder $WORKDIR/.venv $WORKDIR/.venv

# Copy application code from builder
COPY --from=builder $WORKDIR/app $WORKDIR/app

# Copy entrypoint script
COPY scripts/docker_entrypoint.sh scripts/docker_entrypoint.sh

# Add non-root user and give access to video group
RUN useradd --create-home --groups video rpicam \
    && chown -R rpicam:video $WORKDIR
USER rpicam

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8018/ || exit 1

EXPOSE 8018

# Run the FastAPI application
ENTRYPOINT [ "scripts/docker_entrypoint.sh" ]
