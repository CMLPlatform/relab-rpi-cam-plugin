# RPI Camera Plugin

[![CI](https://github.com/CMLPlatform/relab-rpi-cam-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/CMLPlatform/relab-rpi-cam-plugin/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/CMLPlatform/relab-rpi-cam-plugin/graph/badge.svg)](https://codecov.io/gh/CMLPlatform/relab-rpi-cam-plugin)

Device-side software for automated image capture on Raspberry Pi, integrated with the [Reverse Engineering Lab platform](https://cml-relab.org).

## Quick Links

- **[Installation Guide](INSTALL.md)** - hardware setup, pairing, Docker, standalone mode, and troubleshooting
- **[Architecture](ARCHITECTURE.md)** - runtime design, relay flow, auth boundaries, and configuration model
- **[Contributing](CONTRIBUTING.md)** - local development, tests, quality checks, and release workflow
- **[Platform Docs](https://docs.cml-relab.org/user-guides/rpi-cam/)** - camera management in Relab

## What It Does

The plugin runs a lightweight FastAPI server on a Raspberry Pi that:

- captures still images from the connected camera module
- publishes an HLS preview through the local MediaMTX sidecar
- connects to Relab through an outbound WebSocket relay
- exposes local REST endpoints for setup, diagnostics, and direct integrations

Supported hardware is Raspberry Pi 5/4 with Camera Module 3/v2 on Raspberry Pi OS 64-bit.

## Supported Modes

| Mode               | Purpose                                                        |
| ------------------ | -------------------------------------------------------------- |
| **Paired relay**   | The Pi opens an outbound WebSocket relay to the Relab backend. |
| **Local direct**   | LAN/Ethernet clients call the Pi API with `X-API-Key`.         |
| **Backend upload** | Captures are pushed back to the Relab backend.                 |
| **S3 upload**      | Captures are written to a configured S3-compatible bucket.     |

## Getting Started

1. Follow [INSTALL.md](INSTALL.md) to prepare the Pi, generate `compose.override.yml`, and start the service.
1. Set `PAIRING_BACKEND_URL` in `.env`.
1. Read the 6-character pairing code from `/setup` or the `PAIRING READY` log banner.
1. Enter the pairing code in the native Relab app.
1. Visit `http://your-pi-ip:8018/setup` to check pairing, status, and diagnostics.

Use the generated [RPi camera API reference](https://docs.cml-relab.org/api/rpi-cam/) for endpoint-level documentation. The Pi-hosted Swagger/OpenAPI routes are development-only.

The HTTPS-served Relab web frontend cannot auto-probe the Pi's plain-HTTP local API because browsers block mixed content. Use the native app for pairing and direct-mode setup.

## Standalone Mode

The plugin can run without a Relab backend by writing captures to an S3-compatible bucket. The default standalone stack uses a loopback-only RustFS sidecar and `APP_ENV=development` for local HTTP storage. Remote production S3 endpoints require HTTPS.

Captured images, queued retries, dead letters, and preview thumbnails are sensitive local device data. In standalone S3 mode, generated public URLs are reachable wherever the configured bucket, endpoint, or proxy is reachable, so keep image storage private or LAN-only when captures are sensitive.

See [INSTALL.md#standalone-mode](INSTALL.md#standalone-mode) for the complete `.env` example and runtime checks.

## Local Direct Mode

Local mode is enabled by default. On first boot the plugin generates a persistent local API key. Paired Relab apps can retrieve that key through the relay for lower-latency LAN access, and custom local clients can use it with `X-API-Key`.

The local key is an auth gate for the direct interface. It does not register a camera in Relab; relay pairing still does that. See [INSTALL.md#local-direct-mode](INSTALL.md#local-direct-mode) for key retrieval and network notes.

## Observability

The plugin writes redacted structured logs and can ship logs, metrics, and traces to an external observability stack. OTLP tracing is opt-in with `OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT`; remote production OTLP endpoints require HTTPS.

See [INSTALL.md#observability](INSTALL.md#observability) for operator configuration and [ARCHITECTURE.md#observability](ARCHITECTURE.md#observability) for the internal pipeline.

## Troubleshooting

Use [INSTALL.md#troubleshooting](INSTALL.md#troubleshooting) for operator issues. Use [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and test workflow.
