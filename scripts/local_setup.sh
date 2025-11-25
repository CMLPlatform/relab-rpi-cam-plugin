#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status
set -e

# Create a null audio sink to stream null audio to YouTube, if it doesn't already exist
pactl list sinks short | grep -q "nullaudio" || pactl load-module module-null-sink sink_name=nullaudio


# Install uv if not already installed
if ! command -v uv >/dev/null 2>&1; then
	echo "Installing uv (https://astral.sh/uv)..."
	if command -v curl >/dev/null 2>&1; then
		curl -fsSL https://astral.sh/uv/install.sh | sh
	elif command -v wget >/dev/null 2>&1; then
		wget -qO- https://astral.sh/uv/install.sh | sh
	else
		echo "Error: curl or wget required to install uv" >&2
		exit 1
	fi

	# Ensure the freshly-installed uv is on PATH for this run
	export PATH="$HOME/.local/bin/env:$PATH"

	if ! command -v uv >/dev/null 2>&1; then
		echo "uv still not available after installation; please add $HOME/.local/bin to your PATH or install uv manually: https://astral.sh/uv" >&2
		exit 1
	fi
fi

# Create venv (includes system site packages for access to PiCamera2) and sync dependencies
uv venv --system-site-packages
uv sync --frozen --no-cache --no-dev
