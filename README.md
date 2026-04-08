# RPI Camera Plugin

Device-side software for automated image capture on Raspberry Pi, integrated with the [Reverse Engineering Lab platform](https://cml-relab.org).

## Quick Links

- **[Installation Guide](INSTALL.md)** — Hardware, setup, and configuration
- **[Contributing](CONTRIBUTING.md)** — Development workflow and testing
- **[Platform Docs](https://docs.cml-relab.org/user-guides/rpi-cam/)** — Camera management in RELab

## What It Does

The plugin runs a lightweight FastAPI server on your Raspberry Pi that:

- Captures images from the connected camera module
- Streams live preview footage
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
   - `/stream/watch` — Live preview
   - `/docs` — API reference

For platform management and operation, see the [RELab camera guide](https://docs.cml-relab.org/user-guides/rpi-cam/).

## Troubleshooting

**Camera not detected?** Run `rpicam-hello --list-cameras`

**Won't connect?** See [INSTALL.md — Troubleshooting](INSTALL.md#troubleshooting) for connection-specific issues.

**Want to contribute?** See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.
