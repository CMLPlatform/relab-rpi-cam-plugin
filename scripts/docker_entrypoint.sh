#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status, fail on undefined variables, fail on pipe errors
set -euo pipefail

# Start PulseAudio daemon (handles case where already running)
pulseaudio --start --daemonize 2>/dev/null || true

# Create a null audio sink if it doesn't exist (idempotent)
pactl list sinks short 2>/dev/null | grep -q "nullaudio" || \
    pactl load-module module-null-sink sink_name=nullaudio > /dev/null 2>&1 || true

# Run the FastAPI application
exec .venv/bin/fastapi run app/main.py --host 0.0.0.0 --port 8018
