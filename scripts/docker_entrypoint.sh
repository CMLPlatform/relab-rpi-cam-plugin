#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status, fail on undefined variables, fail on pipe errors
set -euo pipefail

# Ensure the persistent relay credentials volume is writable by the app user.
mkdir -p /home/rpicam/.config/relab
mkdir -p /app/data/images /app/logs
chown -R 1000:44 /app/data /app/logs
chown -R 1000:44 /home/rpicam/.config

run_as_rpicam() {
  su -s /bin/bash -c "$*" rpicam
}

# Run the FastAPI application
exec su -s /bin/bash -c '.venv/bin/fastapi run app/main.py --host 0.0.0.0 --port 8018' rpicam
