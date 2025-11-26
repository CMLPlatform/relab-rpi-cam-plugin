#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status
set -euo pipefail

# Usage: ./scripts/local_setup.sh [--dev]
#
# Options:
#   --dev    Create a development environment (install dev deps) and skip
#            installing pre-commit hooks.

DEV_MODE=false
while [ "$#" -gt 0 ]; do
	case "$1" in
		--dev)
			DEV_MODE=true
			shift
			;;
		-h|--help)
			echo "Set up a local environment for the relab-rpi-cam-plugin repository using uv."
			echo "Usage: $0 [--dev]"
			echo ""
			echo "Arguments:"
			echo "  --dev    Create a development environment (install dev deps and install pre-commit hooks)."
			exit 0
			;;
		*)
			shift
			;;
	esac
done

# Create a null audio sink to stream null audio to YouTube, if it doesn't already exist
if command -v pactl >/dev/null 2>&1; then
	if pactl list sinks short | grep -q "nullaudio"; then
		echo "Null audio sink 'nullaudio' already exists"
	else
		echo "Creating null audio sink 'nullaudio'..."
		pactl load-module module-null-sink sink_name=nullaudio
	fi
fi

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

# Create venv (includes system site packages for access to PiCamera2)
uv venv --system-site-packages --clear

# Sync dependencies. In dev mode we keep dev dependencies, otherwise exclude them.
if [ "$DEV_MODE" = true ]; then
	echo "Syncing dependencies (including dev deps)"
	uv sync --frozen --no-cache
else
	echo "Syncing dependencies (excluding dev deps)"
	uv sync --frozen --no-cache --no-dev
fi

# Install pre-commit hooks only in dev mode
if [ "$DEV_MODE" == true ]; then
	uv run pre-commit install
else
	echo "Skipping pre-commit install in non-dev mode"
fi
