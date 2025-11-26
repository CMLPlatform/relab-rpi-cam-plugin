# Use UV base image
FROM ghcr.io/astral-sh/uv:0.9-python3.13-trixie-slim

# Set the working directory inside the container
ARG WORKDIR=/app
WORKDIR $WORKDIR

# Install system dependencies
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eux; \
    apt update && apt full-upgrade -y \
    && apt install -y --no-install-recommends \
    curl \
    dbus \
    ffmpeg \
    git \
    gnupg \
    pulseaudio \
    && apt-get dist-clean

# Add Raspberry Pi OS repository for picamera2
RUN curl -fsSL https://archive.raspberrypi.org/debian/raspberrypi.gpg.key | gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] http://archive.raspberrypi.org/debian/ trixie main" > /etc/apt/sources.list.d/raspi.list

# Install picamera2 system packages
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt update && apt full-upgrade -y && apt install -y --no-install-recommends \
    python3-picamera2 \
    && apt-get dist-clean

# Copy entrypoint script early to avoid invalidating later layers when it doesn't change
COPY scripts/docker_entrypoint.sh scripts/docker_entrypoint.sh

# uv optimizations (see https://docs.astral.sh/uv/guides/integration/docker/#optimizations)
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Copy dependency files
COPY .python-version pyproject.toml uv.lock ./

# Copy shared model files
COPY relab_rpi_cam_models/ relab_rpi_cam_models/

# Install dependencies (see https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --system-site-packages && \
    uv sync --locked --no-install-project --no-editable --no-dev

# Copy application directory
COPY app/ app/

# Final sync with project code (see https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev

# Set Python variables (explicitly add the system pacakges to PYTHONPATH, set unbuffered output, add venv to PATH)
ENV PYTHONPATH="$WORKDIR:/usr/lib/python3/dist-packages"  \
    PYTHONUNBUFFERED=1 \
    PATH="$WORKDIR/.venv/bin:$PATH"

# Add a non-root user and give access to video group, then chown workdir (single layer)
RUN useradd --create-home --groups video rpicam \
    && chown -R rpicam:video $WORKDIR
USER rpicam

# Run the FastAPI application
ENTRYPOINT [ "scripts/docker_entrypoint.sh" ]
