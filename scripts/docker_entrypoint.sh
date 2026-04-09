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

# Start PulseAudio daemon (handles case where already running)
run_as_rpicam "pulseaudio --start --daemonize 2>/dev/null || true"

# Create a null audio sink if it doesn't exist (idempotent)
run_as_rpicam 'pactl list sinks short 2>/dev/null | grep -q "nullaudio" || pactl load-module module-null-sink sink_name=nullaudio > /dev/null 2>&1 || true'

# Run the FastAPI application
exec su -s /bin/bash -c '.venv/bin/fastapi run app/main.py --host 0.0.0.0 --port 8018' rpicam
