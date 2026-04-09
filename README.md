# RPI Camera Plugin

[![CI](https://github.com/CMLPlatform/relab-rpi-cam-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/CMLPlatform/relab-rpi-cam-plugin/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/CMLPlatform/relab-rpi-cam-plugin/graph/badge.svg)](https://codecov.io/gh/CMLPlatform/relab-rpi-cam-plugin)

Device-side software for automated image capture on Raspberry Pi, integrated with the [Reverse Engineering Lab platform](https://cml-relab.org).

## Quick Links

- **[Installation Guide](INSTALL.md)** — Hardware, setup, and configuration
- **[Contributing](CONTRIBUTING.md)** — Development workflow and testing
- **[Platform Docs](https://docs.cml-relab.org/user-guides/rpi-cam/)** — Camera management in RELab

## What It Does

The plugin runs a lightweight FastAPI server on your Raspberry Pi that:

- Captures images from the connected camera module
- Exposes low-resolution snapshot previews for live viewfinder polling
- Connects to the RELab platform (via WebSocket relay or direct HTTP)
- Exposes a REST API for manual testing and integration

Supports **Raspberry Pi 5/4** with **Camera Module 3/v2**, running on Raspberry Pi OS (64-bit) with Python 3.13+.

## Connection Options

| Mode                              | Use When                                | Setup                                       |
| --------------------------------- | --------------------------------------- | ------------------------------------------- |
| **WebSocket Relay** (recommended) | Your Pi doesn't have a public IP        | Just share a pairing code in the RELab app  |
| **Direct HTTP**                   | Your Pi is accessible from the internet | Configure your API key and provide your URL |

## Getting Started

1. [Prepare your Pi and install the plugin](INSTALL.md)
1. Choose a connection method (WebSocket or HTTP)
1. Visit `http://your-pi-ip:8018` to test
   - `/setup` — Pairing and status
   - `/images/preview` — Snapshot preview for viewfinder polling, unavailable while streaming
   - `/stream/watch` — YouTube viewer UI for an active YouTube stream
   - `/docs` — API reference

For headless setup, the active 6-character pairing code is also printed to stdout in a stable `PAIRING READY` log line, so you can read it over SSH, `docker compose logs`, or `journalctl` without opening the browser UI.

Optional observability is available via the `observability` compose profile. When it is enabled, Alloy tails the app's existing file logs and forwards them to Loki for viewing in Grafana. Without that profile, logs are still written to the `app_logs` volume on disk; you just lose the local browsing UI.

For platform management and operation, see the [RELab camera guide](https://docs.cml-relab.org/user-guides/rpi-cam/).

## Troubleshooting

**Camera not detected?** Run `rpicam-hello --list-cameras`

**Won't connect?** See [INSTALL.md — Troubleshooting](INSTALL.md#troubleshooting) for connection-specific issues.

**Want to contribute?** See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.
