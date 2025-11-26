#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status
set -e

# Create a null audio sink to stream null audio to YouTube, if it doesn't already exist
pulseaudio --start --daemonize
pactl list sinks short  | grep -q "nullaudio"|| pactl load-module module-null-sink sink_name=nullaudio > /dev/null 2>&1

# Run the FastAPI application
.venv/bin/fastapi run app/main.py --host 0.0.0.0 --port 8018
