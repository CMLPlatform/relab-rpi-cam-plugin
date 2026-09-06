# Prep stage: fetch the Raspberry Pi archive keyring once (shared by later stages).
# The remote .deb is checksum-pinned; only the extracted keyring file is copied forward.
# See raspberrypi/rpi-image-gen#171 for why we fetch the .deb directly.
FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS rpi-keyring
ADD --checksum=sha256:2e727149d7acb8cc7f604e66d0049161039c8aa1eaf1175e54f9e69d963d60e4 \
    https://archive.raspberrypi.com/debian/pool/main/r/raspberrypi-archive-keyring/raspberrypi-archive-keyring_2025.1+rpt1_all.deb /tmp/keyring.deb
RUN dpkg -i /tmp/keyring.deb

# Build stage: compile the virtual environment (no S3 dependencies by default).
FROM ghcr.io/astral-sh/uv:0.12-python3.13-trixie-slim@sha256:3f30222c158072567236642664d80e232204d92f4912f2424d9fd5acdaa4f788 AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Configure Raspberry Pi repository (keyring from prep stage)
COPY --from=rpi-keyring /usr/share/keyrings/raspberrypi-archive-keyring.gpg /usr/share/keyrings/
RUN printf "Types: deb\nURIs: https://archive.raspberrypi.com/debian/\nSuites: trixie\nComponents: main\nSigned-By: /usr/share/keyrings/raspberrypi-archive-keyring.gpg\n" \
    > /etc/apt/sources.list.d/raspi.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends python3-picamera2 && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies (cached layer — copy lockfile first for layer-cache efficiency).
COPY .python-version pyproject.toml uv.lock README.md ./
COPY relab_rpi_cam_models/ relab_rpi_cam_models/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --system-site-packages && \
    uv sync --locked --no-install-project --no-editable --no-dev

# Install project
COPY app/ app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev

# Standalone builder: add the [s3] group (aioboto3) on top of the paired venv.
# S3CompatibleSink guards its import with try/except so paired-mode images run
# fine without it; this stage is only referenced by the runtime-standalone target.
FROM builder AS builder-standalone
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev --group s3

# Runtime stage: minimal paired-mode image (no S3 dependencies).
# Must stay on 3.13: the venv copied from the builder is a 3.13 venv, and the
# picamera2 extensions in /usr/lib/python3/dist-packages are cpython-313 builds.
# A newer interpreter here silently hides both from ``import``.
FROM python:3.13-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS runtime

WORKDIR /app

# Configure Raspberry Pi repository and install runtime dependencies.
# apt cache mounts keep package lists in the build cache (not in the image
# layer), so there is no need for a separate ``rm -rf /var/lib/apt/lists/*``.
# pip is dropped: the venv is built by uv in the builder stage, so nothing here
# needs it, and its vendored copies of msgpack/setuptools keep tripping Trivy.
COPY --from=rpi-keyring /usr/share/keyrings/raspberrypi-archive-keyring.gpg /usr/share/keyrings/
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    printf "Types: deb\nURIs: https://archive.raspberrypi.com/debian/\nSuites: trixie\nComponents: main\nSigned-By: /usr/share/keyrings/raspberrypi-archive-keyring.gpg\n" \
    > /etc/apt/sources.list.d/raspi.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg python3-picamera2 util-linux && \
    useradd --create-home --uid 1000 --gid video rpicam && \
    pip uninstall -y pip

ENV PYTHONPATH="/app:/usr/lib/python3/dist-packages" \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

COPY --link --chown=1000:44 --from=builder /app/.venv .venv
COPY --link --chown=1000:44 --from=builder /app/app app

RUN mkdir -p /app/data/images /app/logs /home/rpicam/.config/relab && \
    chown -R 1000:44 /app/data /app/logs /home/rpicam/.config

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8018/')" || exit 1

EXPOSE 8018

USER 1000:44

CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8018", "--forwarded-allow-ips", "127.0.0.1,::1"]

# Standalone runtime: identical to the paired runtime but with S3 dependencies.
# Only the venv differs; all other layers are inherited from the runtime stage.
FROM runtime AS runtime-standalone
COPY --link --chown=1000:44 --from=builder-standalone /app/.venv .venv
